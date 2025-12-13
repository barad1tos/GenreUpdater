#!/usr/bin/env python3
"""Diagnose why year lookups failed for pending albums.

This script reads pending_year_verification.csv and queries external APIs
to determine WHY each album failed to resolve. Results are output as JSON
for use by the issue creation script.

Usage:
    python scripts/diagnose_failures.py \
        --input tests/fixtures/pending_year_verification.csv \
        --output diagnostic_results.json \
        --max-albums 10
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Constants
RATE_LIMITED_MSG = "Rate limited (HTTP 429)"


# Data Classes
@dataclass
class ApiDiagnosis:
    """Diagnosis result from a single API."""

    status: str  # "found", "artist_not_found", "album_not_found", "error", "rate_limited"
    details: str
    artist_found: bool = False
    releases_found: int = 0
    close_matches: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] | None = None


@dataclass
class DiagnosisResult:
    """Complete diagnosis for an album."""

    artist: str
    album: str
    first_detected: str
    reason: str

    # API results
    musicbrainz: ApiDiagnosis
    discogs: ApiDiagnosis
    lastfm: ApiDiagnosis

    # Analysis
    possible_causes: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)

    # Metadata
    diagnosis_timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


# =============================================================================
# Text Normalization
# =============================================================================


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text)
    # Lowercase
    text = text.lower()
    # Remove common suffixes
    suffixes = [
        r"\s*\(remaster(ed)?\)",
        r"\s*\(deluxe( edition)?\)",
        r"\s*\(expanded( edition)?\)",
        r"\s*\(anniversary( edition)?\)",
        r"\s*\[\d{4}\]",
        r"\s*\(\d{4}\)",
    ]
    for suffix in suffixes:
        text = re.sub(suffix, "", text, flags=re.IGNORECASE)
    # Remove special characters
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


def has_special_characters(text: str) -> bool:
    """Check if text has characters that might cause API issues."""
    # Check for non-ASCII
    if not text.isascii():
        return True
    # Check for quotes, apostrophes, special punctuation (unique chars only)
    special_chars = {"'", '"', "\u02bb", "\u02bc", "\u02bd", "\u2033", "\u2032", "\u00ab", "\u00bb", "\u2026"}
    return any(c in text for c in special_chars)


def fuzzy_match(needle: str, haystack: list[str]) -> list[str]:
    """Simple fuzzy matching using normalized comparison."""
    needle_norm = normalize_text(needle)
    matches = []

    for item in haystack:
        item_norm = normalize_text(item)
        # Exact normalized match or substring match
        if needle_norm == item_norm or needle_norm in item_norm or item_norm in needle_norm:
            matches.append(item)

    return matches


# =============================================================================
# API Clients (Lightweight for CI)
# =============================================================================


class MusicBrainzDiagnostic:
    """Lightweight MusicBrainz client for diagnostics."""

    BASE_URL = "https://musicbrainz.org/ws/2"
    USER_AGENT = os.environ.get(
        "MUSICBRAINZ_USER_AGENT", "GenreUpdater/2.0 (diagnostic)"
    )

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json",
        }

    async def diagnose(self, artist: str, album: str) -> ApiDiagnosis:
        """Diagnose why MusicBrainz might not find this album."""
        try:
            # Step 1: Search for artist
            artist_results = await self._search_artist(artist)

            if not artist_results:
                return ApiDiagnosis(
                    status="artist_not_found",
                    details=f"No artist found matching '{artist}'",
                )

            # Step 2: Get artist's releases
            artist_id = artist_results[0]["id"]
            artist_name = artist_results[0].get("name", artist)
            releases = await self._get_artist_releases(artist_id)

            if not releases:
                return ApiDiagnosis(
                    status="album_not_found",
                    details=f"Artist '{artist_name}' found but has no releases in database",
                    artist_found=True,
                )

            # Step 3: Try to match album
            release_titles = [r.get("title", "") for r in releases]
            matches = fuzzy_match(album, release_titles)

            if matches:
                return ApiDiagnosis(
                    status="found",
                    details=f"Found {len(matches)} potential matches: {matches[:3]}",
                    artist_found=True,
                    releases_found=len(releases),
                    close_matches=matches[:5],
                )

            return ApiDiagnosis(
                status="album_not_found",
                details=f"Artist has {len(releases)} releases, none match '{album}'",
                artist_found=True,
                releases_found=len(releases),
                close_matches=release_titles[:5],
            )

        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                return ApiDiagnosis(
                    status="rate_limited",
                    details=RATE_LIMITED_MSG,
                )
            return ApiDiagnosis(
                status="error",
                details=f"HTTP {e.status}: {e.message}",
            )
        except Exception as e:
            return ApiDiagnosis(
                status="error",
                details=str(e),
            )

    async def _search_artist(self, artist: str) -> list[dict[str, Any]]:
        """Search for an artist."""
        url = f"{self.BASE_URL}/artist"
        params = {
            "query": f'artist:"{artist}"',
            "fmt": "json",
            "limit": "5",
        }

        await asyncio.sleep(1.1)  # MusicBrainz rate limit: 1 req/sec

        async with self.session.get(
            url, params=params, headers=self.headers
        ) as response:
            response.raise_for_status()
            data: dict[str, Any] = await response.json()
            result = data.get("artists")
            return result if isinstance(result, list) else []

    async def _get_artist_releases(self, artist_id: str) -> list[dict[str, Any]]:
        """Get releases for an artist."""
        url = f"{self.BASE_URL}/release-group"
        params = {
            "artist": artist_id,
            "fmt": "json",
            "limit": "100",
        }

        await asyncio.sleep(1.1)

        async with self.session.get(
            url, params=params, headers=self.headers
        ) as response:
            response.raise_for_status()
            data: dict[str, Any] = await response.json()
            result = data.get("release-groups")
            return result if isinstance(result, list) else []


class DiscogsDiagnostic:
    """Lightweight Discogs client for diagnostics."""

    BASE_URL = "https://api.discogs.com"

    def __init__(self, session: aiohttp.ClientSession, token: str | None) -> None:
        self.session = session
        self.token = token
        self.headers = {
            "User-Agent": "GenreUpdater/2.0",
        }
        if token:
            self.headers["Authorization"] = f"Discogs token={token}"

    async def diagnose(self, artist: str, album: str) -> ApiDiagnosis:
        """Diagnose why Discogs might not find this album."""
        if not self.token:
            return ApiDiagnosis(
                status="error",
                details="DISCOGS_TOKEN not configured",
            )

        try:
            # Search for release
            results = await self._search_release(artist, album)

            if not results:
                # Try artist-only search
                artist_results = await self._search_artist(artist)
                if not artist_results:
                    return ApiDiagnosis(
                        status="artist_not_found",
                        details=f"No results for artist '{artist}'",
                    )
                return ApiDiagnosis(
                    status="album_not_found",
                    details=f"Artist found, but no releases match '{album}'",
                    artist_found=True,
                )

            # Found something
            titles = [r.get("title", "") for r in results[:10]]
            return ApiDiagnosis(
                status="found",
                details=f"Found {len(results)} results",
                artist_found=True,
                releases_found=len(results),
                close_matches=titles[:5],
            )

        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                return ApiDiagnosis(
                    status="rate_limited",
                    details=RATE_LIMITED_MSG,
                )
            if e.status == 401:
                return ApiDiagnosis(
                    status="error",
                    details="Invalid DISCOGS_TOKEN",
                )
            return ApiDiagnosis(
                status="error",
                details=f"HTTP {e.status}: {e.message}",
            )
        except Exception as e:
            return ApiDiagnosis(
                status="error",
                details=str(e),
            )

    async def _search_release(
        self, artist: str, album: str
    ) -> list[dict[str, Any]]:
        """Search for a release."""
        url = f"{self.BASE_URL}/database/search"
        params = {
            "artist": artist,
            "release_title": album,
            "type": "release",
            "per_page": "10",
        }

        await asyncio.sleep(1.0)  # Discogs rate limit

        async with self.session.get(
            url, params=params, headers=self.headers
        ) as response:
            response.raise_for_status()
            data: dict[str, Any] = await response.json()
            result = data.get("results")
            return result if isinstance(result, list) else []

    async def _search_artist(self, artist: str) -> list[dict[str, Any]]:
        """Search for an artist."""
        url = f"{self.BASE_URL}/database/search"
        params = {
            "q": artist,
            "type": "artist",
            "per_page": "5",
        }

        await asyncio.sleep(1.0)

        async with self.session.get(
            url, params=params, headers=self.headers
        ) as response:
            response.raise_for_status()
            data: dict[str, Any] = await response.json()
            result = data.get("results")
            return result if isinstance(result, list) else []


class LastFmDiagnostic:
    """Lightweight Last.fm client for diagnostics."""

    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, session: aiohttp.ClientSession, api_key: str | None) -> None:
        self.session = session
        self.api_key = api_key

    async def diagnose(self, artist: str, album: str) -> ApiDiagnosis:
        """Diagnose why Last.fm might not find this album."""
        if not self.api_key:
            return ApiDiagnosis(
                status="error",
                details="LASTFM_API_KEY not configured",
            )

        try:
            # Try to get album info
            album_info = await self._get_album_info(artist, album)

            if album_info:
                name = album_info.get("name", album)
                playcount = album_info.get("playcount", "unknown")
                return ApiDiagnosis(
                    status="found",
                    details=f"Album found: '{name}' ({playcount} plays)",
                    artist_found=True,
                    releases_found=1,
                )

            # Album not found, check if artist exists
            artist_info = await self._get_artist_info(artist)
            if artist_info:
                return ApiDiagnosis(
                    status="album_not_found",
                    details=f"Artist found, but album '{album}' not in Last.fm",
                    artist_found=True,
                )

            return ApiDiagnosis(
                status="artist_not_found",
                details=f"Artist '{artist}' not found in Last.fm",
            )

        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                return ApiDiagnosis(
                    status="rate_limited",
                    details=RATE_LIMITED_MSG,
                )
            return ApiDiagnosis(
                status="error",
                details=f"HTTP {e.status}: {e.message}",
            )
        except Exception as e:
            return ApiDiagnosis(
                status="error",
                details=str(e),
            )

    async def _get_album_info(
        self, artist: str, album: str
    ) -> dict[str, Any] | None:
        """Get album info from Last.fm."""
        params: dict[str, str] = {
            "method": "album.getinfo",
            "api_key": self.api_key or "",
            "artist": artist,
            "album": album,
            "format": "json",
        }

        async with self.session.get(self.BASE_URL, params=params) as response:
            data: dict[str, Any] = await response.json()
            return None if "error" in data else data.get("album")

    async def _get_artist_info(self, artist: str) -> dict[str, Any] | None:
        """Get artist info from Last.fm."""
        params: dict[str, str] = {
            "method": "artist.getinfo",
            "api_key": self.api_key or "",
            "artist": artist,
            "format": "json",
        }

        async with self.session.get(self.BASE_URL, params=params) as response:
            data: dict[str, Any] = await response.json()
            return None if "error" in data else data.get("artist")


# =============================================================================
# Analysis
# =============================================================================


def _check_artist_not_found(
    artist: str,
    diagnoses: list[ApiDiagnosis],
    causes: list[str],
    actions: list[str],
) -> None:
    """Check if artist was not found in any database."""
    if all(
        not d.artist_found
        for d in diagnoses
        if d.status not in ("error", "rate_limited")
    ):
        causes.append("Artist not found in any database")
        if has_special_characters(artist):
            causes.append("Artist name contains special characters that may cause search issues")
        actions.extend([
            "Verify artist name spelling and try variants",
            f"Search manually: https://musicbrainz.org/search?query={artist.replace(' ', '+')}&type=artist",
        ])


def _check_album_not_found(
    album: str,
    artist: str,
    diagnoses: list[ApiDiagnosis],
    causes: list[str],
    actions: list[str],
) -> None:
    """Check if artist was found but album was not."""
    if any(d.artist_found and d.status == "album_not_found" for d in diagnoses):
        causes.append("Artist exists but album not found")
        if has_special_characters(album):
            causes.append("Album name contains special characters: check apostrophes, quotes")
        if all_matches := [
            match for d in diagnoses for match in d.close_matches
        ]:
            causes.append(f"Potential matches found: {list(set(all_matches))[:3]}")
        actions.extend([
            "Check album name variants (remastered, deluxe, etc.)",
            f"Search manually: https://musicbrainz.org/search?query={album.replace(' ', '+')}+{artist.replace(' ', '+')}&type=release",
        ])


def _check_album_obscure(
    diagnoses: list[ApiDiagnosis],
    causes: list[str],
    actions: list[str],
) -> None:
    """Check if album is too new or obscure."""
    if all(
        d.status in ("album_not_found", "artist_not_found")
        for d in diagnoses
        if d.status not in ("error", "rate_limited")
    ):
        causes.append("Album may be too new or too obscure for databases")
        actions.extend([
            "Consider adding manual year override in config.yaml",
            "Report missing album to MusicBrainz",
        ])


def _check_rate_limiting(
    mb: ApiDiagnosis,
    discogs: ApiDiagnosis,
    lastfm: ApiDiagnosis,
    causes: list[str],
) -> None:
    """Check for rate limiting."""
    if rate_limited := [
        name
        for name, d in [
            ("MusicBrainz", mb),
            ("Discogs", discogs),
            ("Last.fm", lastfm),
        ]
        if d.status == "rate_limited"
    ]:
        causes.append(f"Rate limited by: {', '.join(rate_limited)}")
def _check_api_errors(
    mb: ApiDiagnosis,
    discogs: ApiDiagnosis,
    lastfm: ApiDiagnosis,
    causes: list[str],
    actions: list[str],
) -> None:
    """Check for API errors."""
    if errors := [
        (name, d.details)
        for name, d in [
            ("MusicBrainz", mb),
            ("Discogs", discogs),
            ("Last.fm", lastfm),
        ]
        if d.status == "error"
    ]:
        causes.extend(f"{name} error: {detail}" for name, detail in errors)
        if any("not configured" in str(e[1]) for e in errors):
            actions.append("Configure missing API credentials")
        causes.extend(f"{name} error: {detail}" for name, detail in errors)
        if any("not configured" in str(e[1]) for e in errors):
            actions.append("Configure missing API credentials")


def analyze_failure_patterns(
    artist: str,
    album: str,
    mb: ApiDiagnosis,
    discogs: ApiDiagnosis,
    lastfm: ApiDiagnosis,
) -> tuple[list[str], list[str]]:
    """Analyze API results to determine likely causes and suggest actions."""
    causes: list[str] = []
    actions: list[str] = []
    diagnoses = [mb, discogs, lastfm]

    _check_artist_not_found(artist, diagnoses, causes, actions)
    _check_album_not_found(album, artist, diagnoses, causes, actions)
    _check_album_obscure(diagnoses, causes, actions)
    _check_rate_limiting(mb, discogs, lastfm, causes)
    _check_api_errors(mb, discogs, lastfm, causes, actions)

    # Default actions if nothing specific
    if not actions:
        actions.append("Manual investigation required")

    return causes, actions


# =============================================================================
# Main
# =============================================================================


async def diagnose_album(
    artist: str,
    album: str,
    first_detected: str,
    reason: str,
    mb_client: MusicBrainzDiagnostic,
    discogs_client: DiscogsDiagnostic,
    lastfm_client: LastFmDiagnostic,
) -> DiagnosisResult:
    """Run full diagnosis on a single album."""
    logger.info("Diagnosing: %s - %s", artist, album)

    # Run API diagnostics (sequentially to respect rate limits)
    mb_result = await mb_client.diagnose(artist, album)
    discogs_result = await discogs_client.diagnose(artist, album)
    lastfm_result = await lastfm_client.diagnose(artist, album)

    # Analyze patterns
    causes, actions = analyze_failure_patterns(
        artist, album, mb_result, discogs_result, lastfm_result
    )

    return DiagnosisResult(
        artist=artist,
        album=album,
        first_detected=first_detected,
        reason=reason,
        musicbrainz=mb_result,
        discogs=discogs_result,
        lastfm=lastfm_result,
        possible_causes=causes,
        suggested_actions=actions,
    )


async def _run_diagnostics(
    pending_albums: list[dict[str, str]],
    discogs_token: str | None,
    lastfm_api_key: str | None,
) -> list[DiagnosisResult]:
    """Run async diagnostics on pending albums."""
    results: list[DiagnosisResult] = []

    async with aiohttp.ClientSession() as session:
        mb_client = MusicBrainzDiagnostic(session)
        discogs_client = DiscogsDiagnostic(session, discogs_token)
        lastfm_client = LastFmDiagnostic(session, lastfm_api_key)

        for album_data in pending_albums:
            artist = album_data.get("artist", "")
            album = album_data.get("album", "")
            timestamp = album_data.get("timestamp", "")
            reason = album_data.get("reason", "unknown")

            if not artist or not album:
                logger.warning("Skipping invalid entry: %s", album_data)
                continue

            result = await diagnose_album(
                artist=artist,
                album=album,
                first_detected=timestamp,
                reason=reason,
                mb_client=mb_client,
                discogs_client=discogs_client,
                lastfm_client=lastfm_client,
            )
            results.append(result)

    return results


def _serialize_results(results: list[DiagnosisResult]) -> list[dict[str, Any]]:
    """Serialize diagnosis results to dict format."""
    output_data = []
    for r in results:
        data = asdict(r)
        # Convert nested dataclasses
        data["musicbrainz"] = asdict(r.musicbrainz)
        data["discogs"] = asdict(r.discogs)
        data["lastfm"] = asdict(r.lastfm)
        output_data.append(data)
    return output_data


def main(
    input_file: Path,
    output_file: Path,
    max_albums: int,
) -> None:
    """Main entry point."""
    # Load pending albums (sync I/O)
    if not input_file.exists():
        logger.error("Input file not found: %s", input_file)
        sys.exit(1)

    with input_file.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        pending_albums = list(reader)

    logger.info("Loaded %d pending albums", len(pending_albums))

    if max_albums > 0:
        pending_albums = pending_albums[:max_albums]
        logger.info("Limited to %d albums", max_albums)

    # Get API credentials from environment
    discogs_token = os.environ.get("DISCOGS_TOKEN")
    lastfm_api_key = os.environ.get("LASTFM_API_KEY")

    if not discogs_token:
        logger.warning("DISCOGS_TOKEN not set - Discogs diagnosis will be limited")
    if not lastfm_api_key:
        logger.warning("LASTFM_API_KEY not set - Last.fm diagnosis will be limited")

    # Run async diagnostics
    results = asyncio.run(_run_diagnostics(pending_albums, discogs_token, lastfm_api_key))

    # Serialize and write output (sync I/O)
    output_data = _serialize_results(results)
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    logger.info("Wrote %d diagnosis results to %s", len(results), output_file)

    # Summary
    found_count = sum(
        any(
            getattr(r, api).status == "found"
            for api in ["musicbrainz", "discogs", "lastfm"]
        )
        for r in results
    )
    logger.info("Summary: %d/%d albums found in at least one API", found_count, len(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnose year lookup failures")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("tests/fixtures/pending_year_verification.csv"),
        help="Input CSV file with pending albums",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("diagnostic_results.json"),
        help="Output JSON file for results",
    )
    parser.add_argument(
        "--max-albums",
        type=int,
        default=0,
        help="Maximum albums to diagnose (0 = all)",
    )

    args = parser.parse_args()
    main(args.input, args.output, args.max_albums)
