"""Generate JSON fixture files for Swift parity tests.

Produces 5 fixture files that capture Python implementation behavior
for cross-validation against the Swift port.

Usage:
    uv run python tools/generate_swift_fixtures.py --output /path/to/Fixtures/
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Add project source to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import yaml  # noqa: E402

from core.models.metadata_utils import determine_dominant_genre_for_artist  # noqa: E402
from core.models.normalization import normalize_for_matching  # noqa: E402
from core.models.track_models import ScoringConfig, TrackDict  # noqa: E402
from core.tracks.year_consistency import YearConsistencyChecker  # noqa: E402
from services.api.year_score_resolver import YearScoreResolver  # noqa: E402
from services.api.year_scoring import ReleaseScorer  # noqa: E402

# Quiet logger for deterministic output
_logger = logging.getLogger("fixture_gen")
_logger.addHandler(logging.NullHandler())

# Test data constants
_MICHAEL_JACKSON = "Michael Jackson"
_THRILLER = "Thriller"
_DAKHABRAKHA = "ДахаБраха"
_DAKHABRAKHA_LATIN = "DakhaBrakha"
_TINI_ZABUTYKH = "Тіні забутих предків"
_ZUTOMAYO = "ずっと真夜中でいいのに。"
_ZUTOMAYO_ALBUM = "花と水飴、最終電車"
_NEW_BAND = "New Band"
_OK_COMPUTER = "OK Computer"
_ABBEY_ROAD = "Abbey Road"
_THE_BEATLES = "The Beatles"
_DARK_SIDE = "The Dark Side of the Moon"
_PINK_FLOYD = "Pink Floyd"

_REMASTER_KEYWORDS = frozenset(["remaster", "deluxe", "edition", "anniversary"])


def _load_library_snapshot() -> list[dict[str, Any]]:
    """Load the real library snapshot for genre fixtures."""
    snapshot_path = PROJECT_ROOT / "tests" / "fixtures" / "library_snapshot.json"
    with snapshot_path.open(encoding="utf-8") as f:
        return json.load(f)


def _has_cjk(text: str) -> bool:
    """Check if text contains CJK or Korean characters."""
    return any("\u3000" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7ff" for c in text)


def _has_cyrillic(text: str) -> bool:
    """Check if text contains Cyrillic characters."""
    return any("\u0400" <= c <= "\u04ff" for c in text)


def _detect_script_category(artist: str, album: str) -> str:
    """Detect the script category for an artist+album pair."""
    combined = artist + album
    if _has_cjk(combined):
        return "cjk"
    return "cyrillic" if _has_cyrillic(combined) else "latin"


def _is_old_year(year_str: str) -> bool:
    """Check if year string represents a pre-1970 year."""
    if not year_str or year_str == "0":
        return False
    with contextlib.suppress(ValueError):
        return int(year_str) < 1970
    return False


def _try_append(
    category: list[list[dict[str, Any]]],
    album_tracks: list[dict[str, Any]],
    limit: int,
) -> None:
    """Append album_tracks to category if under the limit."""
    if len(category) < limit:
        category.append(album_tracks)


def _classify_album(
    album_tracks: list[dict[str, Any]],
    categories: dict[str, list[list[dict[str, Any]]]],
) -> None:
    """Classify album tracks into diversity categories for representative sampling."""
    first = album_tracks[0]
    artist = str(first.get("artist", ""))
    album = str(first.get("album", ""))
    genre = str(first.get("genre", ""))
    year_str = str(first.get("year", ""))

    script_cat = _detect_script_category(artist, album)
    script_limits = {"cjk": 8, "cyrillic": 8, "latin": 10}
    _try_append(categories[script_cat], album_tracks, script_limits[script_cat])

    album_lower = album.lower()
    if any(kw in album_lower for kw in _REMASTER_KEYWORDS):
        _try_append(categories["remaster"], album_tracks, 5)

    if _is_old_year(year_str):
        _try_append(categories["old_year"], album_tracks, 5)

    if not genre or not genre.strip():
        _try_append(categories["no_genre"], album_tracks, 5)

    track_count = len(album_tracks)
    if track_count == 1:
        _try_append(categories["single_track"], album_tracks, 3)
    elif track_count >= 20:
        _try_append(categories["large_album"], album_tracks, 3)


def _build_track_dict(raw: dict[str, Any]) -> TrackDict | None:
    """Build a TrackDict from raw data, returning None on failure."""
    try:
        year_raw = raw.get("year")
        return TrackDict(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            artist=str(raw.get("artist", "")),
            album=str(raw.get("album", "")),
            genre=raw.get("genre"),
            year=str(year_raw) if year_raw else None,
            date_added=raw.get("date_added"),
            album_artist=raw.get("album_artist"),
            track_status=raw.get("track_status"),
            release_year=raw.get("release_year"),
        )
    except Exception as exc:
        _logger.debug("Failed to build TrackDict: %s", exc)
        return None


def _pick_representative_albums(
    tracks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select ~50 representative albums covering diverse scripts and edge cases."""
    albums: dict[str, list[dict[str, Any]]] = {}
    for t in tracks:
        album = t.get("album", "")
        artist = t.get("artist", "")
        key = f"{artist}|||{album}"
        albums.setdefault(key, []).append(t)

    categories: dict[str, list[list[dict[str, Any]]]] = {
        "cjk": [],
        "cyrillic": [],
        "latin": [],
        "remaster": [],
        "old_year": [],
        "no_genre": [],
        "single_track": [],
        "large_album": [],
    }

    for album_tracks in albums.values():
        _classify_album(album_tracks, categories)

    selected: list[dict[str, Any]] = []
    for cat_tracks in categories.values():
        for album_tracks in cat_tracks:
            selected.extend(album_tracks)

    return selected


def _track_to_fixture(track: dict[str, Any]) -> dict[str, Any]:
    """Convert a Python track dict to a minimal JSON-serializable fixture."""
    return {
        "id": str(track.get("id", "")),
        "name": str(track.get("name", "")),
        "artist": str(track.get("artist", "")),
        "album": str(track.get("album", "")),
        "genre": track.get("genre"),
        "year": track.get("year"),
        "dateAdded": track.get("date_added"),
        "releaseYear": track.get("release_year"),
        "albumArtist": track.get("album_artist"),
        "trackStatus": track.get("track_status"),
    }


def _map_release_type(raw: str) -> str:
    """Map Python release type string to Swift ReleaseType raw value."""
    mapping = {
        "album": "album",
        "ep": "ep",
        "single": "single",
        "compilation": "compilation",
        "live": "live",
        "soundtrack": "soundtrack",
        "remix": "remix",
    }
    return mapping.get(raw.lower(), "other")


def _map_release_status(raw: str) -> str:
    """Map Python release status string to Swift ReleaseStatus raw value."""
    mapping = {
        "official": "official",
        "bootleg": "bootleg",
        "promotion": "promotional",
        "promotional": "promotional",
        "promo": "promotional",
        "pseudorelease": "pseudo-release",
    }
    return mapping.get(raw.lower(), "other")


def _extract_rg_year(date_str: str | None) -> int | None:
    """Extract year from RG first date string."""
    if not date_str:
        return None
    try:
        return int(date_str.split("-")[0])
    except (ValueError, IndexError):
        return None


def _build_release_fixture(release: dict[str, Any]) -> dict[str, Any]:
    """Build release fixture dict from raw release data."""
    year_str = str(release.get("year", ""))
    return {
        "artist": str(release.get("artist", "")),
        "album": str(release.get("title", "")),
        "year": int(year_str) if year_str.isdigit() else 0,
        "source": str(release.get("source", "unknown")),
        "releaseType": _map_release_type(str(release.get("album_type", ""))),
        "status": _map_release_status(str(release.get("status", ""))),
        "country": release.get("country"),
        "isReissue": bool(release.get("is_reissue", False)),
        "mbReleaseGroupID": release.get("mb_release_group_id"),
        "mbReleaseGroupFirstYear": _extract_rg_year(release.get("releasegroup_first_date")),
        "genre": release.get("genre"),
    }


def _find_earliest_album(track_dicts: list[TrackDict]) -> str | None:
    """Find the earliest-added album name from a list of TrackDicts."""
    album_earliest: dict[str, tuple[str, str]] = {}
    for td in track_dicts:
        album_name = td.album
        date_added = td.date_added or ""
        if album_name not in album_earliest or date_added < album_earliest[album_name][0]:
            album_earliest[album_name] = (date_added, td.genre or "")
    if not album_earliest:
        return None
    return min(album_earliest.items(), key=lambda x: x[1][0])[0]


def _group_tracks_by_artist(tracks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group raw tracks by normalized artist name."""
    artist_groups: dict[str, list[dict[str, Any]]] = {}
    for t in tracks:
        album_artist = t.get("album_artist", "")
        artist = album_artist if album_artist and str(album_artist).strip() else t.get("artist", "Unknown")
        key = normalize_for_matching(str(artist))
        artist_groups.setdefault(key, []).append(t)
    return artist_groups


# 1. Genre Reference Fixtures
def generate_genre_fixtures(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate genre determination fixtures from real library data."""
    artist_groups = _group_tracks_by_artist(tracks)
    fixtures: list[dict[str, Any]] = []
    selected_keys = list(artist_groups.keys())[:35]

    for key in selected_keys:
        group = artist_groups[key]
        track_dicts = [td for td in (_build_track_dict(t) for t in group) if td is not None]
        if not track_dicts:
            continue

        result_genre = determine_dominant_genre_for_artist(track_dicts, _logger)
        source_album = _find_earliest_album(track_dicts) if result_genre != "Unknown" else None

        fixtures.append(
            {
                "id": f"genre_{len(fixtures):03d}",
                "description": f"Artist group: {group[0].get('artist', 'Unknown')}",
                "tracks": [_track_to_fixture(t) for t in group],
                "expected": {
                    "genre": result_genre if result_genre != "Unknown" else None,
                    "sourceAlbum": source_album,
                },
            }
        )

    fixtures.extend(_genre_edge_cases())
    return fixtures


def _genre_edge_cases() -> list[dict[str, Any]]:
    """Synthetic edge cases for genre determination."""
    return [
        {
            "id": "genre_edge_empty",
            "description": "Empty track list returns nil",
            "tracks": [],
            "expected": {"genre": None, "sourceAlbum": None},
        },
        {
            "id": "genre_edge_no_genre",
            "description": "All tracks have nil genre",
            "tracks": [
                {"id": "e1", "name": "Song", "artist": "Artist", "album": "Album", "genre": None, "year": "2020", "dateAdded": "2020-01-01 00:00:00"},
            ],
            "expected": {"genre": None, "sourceAlbum": None},
        },
        {
            "id": "genre_edge_single",
            "description": "Single track with genre",
            "tracks": [
                {
                    "id": "e2",
                    "name": "Song",
                    "artist": "Artist",
                    "album": "Album",
                    "genre": "Rock",
                    "year": "2020",
                    "dateAdded": "2020-01-01 00:00:00",
                },
            ],
            "expected": {"genre": "Rock", "sourceAlbum": "Album"},
        },
        {
            "id": "genre_edge_multi_album",
            "description": "Multiple albums, earliest date wins",
            "tracks": [
                {
                    "id": "e3",
                    "name": "New Song",
                    "artist": "Artist",
                    "album": "New Album",
                    "genre": "Pop",
                    "year": "2023",
                    "dateAdded": "2023-06-01 00:00:00",
                },
                {
                    "id": "e4",
                    "name": "Old Song",
                    "artist": "Artist",
                    "album": "Old Album",
                    "genre": "Rock",
                    "year": "2010",
                    "dateAdded": "2010-03-15 00:00:00",
                },
            ],
            "expected": {"genre": "Rock", "sourceAlbum": "Old Album"},
        },
    ]


# 2. Year Scoring Reference Fixtures
def generate_scoring_fixtures() -> list[dict[str, Any]]:
    """Generate year scoring fixtures using Python's ReleaseScorer."""
    config_path = PROJECT_ROOT / "config.yaml"
    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    scoring_cfg = ScoringConfig(**config["year_retrieval"]["scoring"])

    scorer = ReleaseScorer(
        scoring_config=scoring_cfg,
        console_logger=_logger,
    )

    fixtures: list[dict[str, Any]] = []

    # Define test releases covering all scoring paths
    test_cases: list[dict[str, Any]] = [
        # Perfect match cases
        {
            "id": "score_perfect_match",
            "description": "Perfect artist + album match, official album, MusicBrainz",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "1982",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Artist exact, album exact, different source
        {
            "id": "score_discogs_source",
            "description": "Same match quality but from Discogs",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "1982",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "discogs",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # iTunes source
        {
            "id": "score_itunes_source",
            "description": "Same match quality but from iTunes",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "1982",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "itunes",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Artist mismatch
        {
            "id": "score_artist_mismatch",
            "description": "Different artist entirely",
            "release": {
                "title": _THRILLER,
                "artist": "Someone Else",
                "year": "1982",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Album substring match
        {
            "id": "score_album_substring",
            "description": "Album is substring (deluxe edition)",
            "release": {
                "title": "Thriller (Deluxe Edition)",
                "artist": _MICHAEL_JACKSON,
                "year": "2008",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Album unrelated
        {
            "id": "score_album_unrelated",
            "description": "Completely unrelated album",
            "release": {
                "title": "Some Other Album",
                "artist": _MICHAEL_JACKSON,
                "year": "1991",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # EP type
        {
            "id": "score_ep_type",
            "description": "EP release type penalty",
            "release": {
                "title": "Thriller EP",
                "artist": _MICHAEL_JACKSON,
                "year": "1982",
                "album_type": "EP",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": "Thriller EP",
            "artistRegion": "US",
        },
        # Compilation type
        {
            "id": "score_compilation_type",
            "description": "Compilation release type penalty",
            "release": {
                "title": "Greatest Hits",
                "artist": _MICHAEL_JACKSON,
                "year": "1995",
                "album_type": "Compilation",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": "Greatest Hits",
            "artistRegion": "US",
        },
        # Bootleg status
        {
            "id": "score_bootleg_status",
            "description": "Bootleg release status penalty",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "1983",
                "album_type": "Album",
                "status": "Bootleg",
                "country": "DE",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Promo status
        {
            "id": "score_promo_status",
            "description": "Promotional release status penalty",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "1982",
                "album_type": "Album",
                "status": "Promotion",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Reissue
        {
            "id": "score_reissue",
            "description": "Reissue penalty",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "2001",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "is_reissue": True,
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Country mismatch but major market
        {
            "id": "score_major_market",
            "description": "Country is major market but not artist's home",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "1982",
                "album_type": "Album",
                "status": "Official",
                "country": "GB",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Future year
        {
            "id": "score_future_year",
            "description": "Future year penalty",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": str(datetime.now(tz=UTC).year + 1),
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Invalid year (should return 0)
        {
            "id": "score_invalid_year",
            "description": "Invalid year returns score 0",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "abc",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Empty year
        {
            "id": "score_empty_year",
            "description": "Empty year returns score 0",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # RG first date match (MusicBrainz only)
        {
            "id": "score_rg_match",
            "description": "Release group first date matches year",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "1982",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
                "releasegroup_first_date": "1982-11-30",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # RG first date mismatch (year diff penalty)
        {
            "id": "score_rg_mismatch",
            "description": "Release year differs from RG first year",
            "release": {
                "title": _THRILLER,
                "artist": _MICHAEL_JACKSON,
                "year": "1985",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
                "releasegroup_first_date": "1982-11-30",
            },
            "queryArtist": _MICHAEL_JACKSON,
            "queryAlbum": _THRILLER,
            "artistRegion": "US",
        },
        # Soundtrack compensation
        {
            "id": "score_soundtrack",
            "description": "Soundtrack artist compensation",
            "release": {
                "title": "Interstellar",
                "artist": "Hans Zimmer",
                "year": "2014",
                "album_type": "Soundtrack",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
                "genre": "Soundtrack",
            },
            "queryArtist": "Various Artists",
            "queryAlbum": "Interstellar",
            "artistRegion": None,
        },
        # CJK artist
        {
            "id": "score_cjk_artist",
            "description": "Japanese artist exact match",
            "release": {
                "title": _ZUTOMAYO_ALBUM,
                "artist": _ZUTOMAYO,
                "year": "2021",
                "album_type": "Album",
                "status": "Official",
                "country": "JP",
                "source": "musicbrainz",
            },
            "queryArtist": _ZUTOMAYO,
            "queryAlbum": _ZUTOMAYO_ALBUM,
            "artistRegion": "JP",
        },
        # Cyrillic artist
        {
            "id": "score_cyrillic_artist",
            "description": "Ukrainian artist exact match",
            "release": {
                "title": _TINI_ZABUTYKH,
                "artist": _DAKHABRAKHA,
                "year": "2016",
                "album_type": "Album",
                "status": "Official",
                "country": "UA",
                "source": "musicbrainz",
            },
            "queryArtist": _DAKHABRAKHA,
            "queryAlbum": _TINI_ZABUTYKH,
            "artistRegion": "UA",
        },
        # Cross-script match (Latin query, Cyrillic release)
        {
            "id": "score_cross_script",
            "description": "Cross-script artist penalty",
            "release": {
                "title": _TINI_ZABUTYKH,
                "artist": _DAKHABRAKHA,
                "year": "2016",
                "album_type": "Album",
                "status": "Official",
                "country": "UA",
                "source": "itunes",
            },
            "queryArtist": _DAKHABRAKHA_LATIN,
            "queryAlbum": _TINI_ZABUTYKH,
            "artistRegion": "UA",
        },
        # Artist period context - year before start
        {
            "id": "score_before_artist_start",
            "description": "Year before artist career start",
            "release": {
                "title": "First Album",
                "artist": _NEW_BAND,
                "year": "1990",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _NEW_BAND,
            "queryAlbum": "First Album",
            "artistRegion": "US",
            "artistPeriod": {"start_year": 2000, "end_year": None},
        },
        # Artist period context - year near start
        {
            "id": "score_near_artist_start",
            "description": "Year near artist career start (bonus)",
            "release": {
                "title": "Debut",
                "artist": _NEW_BAND,
                "year": "2000",
                "album_type": "Album",
                "status": "Official",
                "country": "US",
                "source": "musicbrainz",
            },
            "queryArtist": _NEW_BAND,
            "queryAlbum": "Debut",
            "artistRegion": "US",
            "artistPeriod": {"start_year": 2000, "end_year": None},
        },
        # No country info
        {
            "id": "score_no_country",
            "description": "No country information",
            "release": {"title": "Album", "artist": "Artist", "year": "2020", "album_type": "Album", "status": "Official", "source": "musicbrainz"},
            "queryArtist": "Artist",
            "queryAlbum": "Album",
            "artistRegion": None,
        },
    ]

    # Multi-candidate ranking sets
    ranking_sets: list[dict[str, Any]] = [
        {
            "id": "ranking_source_preference",
            "description": "Same release, different sources - MusicBrainz should rank highest",
            "candidates": [
                {
                    "title": _OK_COMPUTER,
                    "artist": "Radiohead",
                    "year": "1997",
                    "album_type": "Album",
                    "status": "Official",
                    "country": "GB",
                    "source": "discogs",
                },
                {
                    "title": _OK_COMPUTER,
                    "artist": "Radiohead",
                    "year": "1997",
                    "album_type": "Album",
                    "status": "Official",
                    "country": "GB",
                    "source": "musicbrainz",
                },
                {
                    "title": _OK_COMPUTER,
                    "artist": "Radiohead",
                    "year": "1997",
                    "album_type": "Album",
                    "status": "Official",
                    "country": "GB",
                    "source": "itunes",
                },
            ],
            "queryArtist": "Radiohead",
            "queryAlbum": _OK_COMPUTER,
            "artistRegion": "GB",
        },
        {
            "id": "ranking_type_preference",
            "description": "Same album, different types - Album should rank highest",
            "candidates": [
                {
                    "title": _ABBEY_ROAD,
                    "artist": _THE_BEATLES,
                    "year": "1969",
                    "album_type": "Compilation",
                    "status": "Official",
                    "country": "GB",
                    "source": "musicbrainz",
                },
                {
                    "title": _ABBEY_ROAD,
                    "artist": _THE_BEATLES,
                    "year": "1969",
                    "album_type": "Album",
                    "status": "Official",
                    "country": "GB",
                    "source": "musicbrainz",
                },
                {
                    "title": _ABBEY_ROAD,
                    "artist": _THE_BEATLES,
                    "year": "1969",
                    "album_type": "EP",
                    "status": "Official",
                    "country": "GB",
                    "source": "musicbrainz",
                },
            ],
            "queryArtist": _THE_BEATLES,
            "queryAlbum": _ABBEY_ROAD,
            "artistRegion": "GB",
        },
        {
            "id": "ranking_original_vs_reissue",
            "description": "Original release should rank higher than reissue",
            "candidates": [
                {
                    "title": _DARK_SIDE,
                    "artist": _PINK_FLOYD,
                    "year": "2003",
                    "album_type": "Album",
                    "status": "Official",
                    "country": "US",
                    "is_reissue": True,
                    "source": "musicbrainz",
                },
                {
                    "title": _DARK_SIDE,
                    "artist": _PINK_FLOYD,
                    "year": "1973",
                    "album_type": "Album",
                    "status": "Official",
                    "country": "GB",
                    "source": "musicbrainz",
                },
            ],
            "queryArtist": _PINK_FLOYD,
            "queryAlbum": _DARK_SIDE,
            "artistRegion": "GB",
        },
    ]

    # Score individual test cases
    for case in test_cases:
        release: dict[str, Any] = case["release"]
        query_artist = str(case["queryArtist"])
        query_album = str(case["queryAlbum"])
        artist_norm = normalize_for_matching(query_artist)
        album_norm = normalize_for_matching(query_album)

        # Set artist period context if provided
        if "artistPeriod" in case:
            scorer.artist_period_context = case["artistPeriod"]
        else:
            scorer.artist_period_context = None

        score = scorer.score_original_release(
            release=release,
            artist_norm=artist_norm,
            album_norm=album_norm,
            artist_region=case.get("artistRegion"),
            source=str(release.get("source", "unknown")),
            album_orig=query_album,
        )

        fixtures.append(
            {
                "id": str(case["id"]),
                "description": str(case["description"]),
                "release": _build_release_fixture(release),
                "query": {
                    "artist": query_artist,
                    "album": query_album,
                    "artistRegion": case.get("artistRegion"),
                    "artistPeriodStart": case.get("artistPeriod", {}).get("start_year"),
                    "artistPeriodEnd": case.get("artistPeriod", {}).get("end_year"),
                },
                "expected": {
                    "totalScore": score,
                },
            }
        )

    # Score ranking sets
    for rset in ranking_sets:
        query_artist = str(rset["queryArtist"])
        query_album = str(rset["queryAlbum"])
        artist_norm = normalize_for_matching(query_artist)
        album_norm = normalize_for_matching(query_album)
        scorer.artist_period_context = None

        scored: list[dict[str, Any]] = []
        for cand in rset["candidates"]:
            cand_release: dict[str, Any] = cand
            score = scorer.score_original_release(
                release=cand_release,
                artist_norm=artist_norm,
                album_norm=album_norm,
                artist_region=rset.get("artistRegion"),
                source=str(cand_release.get("source", "unknown")),
                album_orig=query_album,
            )
            scored.append(
                {
                    "release": _build_release_fixture(cand_release),
                    "totalScore": score,
                }
            )

        # Sort by score descending for expected ranking
        scored.sort(key=lambda x: -x["totalScore"])

        fixtures.append(
            {
                "id": str(rset["id"]),
                "description": str(rset["description"]),
                "type": "ranking",
                "query": {
                    "artist": query_artist,
                    "album": query_album,
                    "artistRegion": rset.get("artistRegion"),
                    "artistPeriodStart": None,
                    "artistPeriodEnd": None,
                },
                "candidates": scored,
                "expectedRanking": [s["release"]["source"] for s in scored],
            }
        )

    return fixtures


# 3. Year Resolution Reference Fixtures
def generate_resolution_fixtures() -> list[dict[str, Any]]:
    """Generate year score resolution fixtures."""
    resolver = YearScoreResolver(
        min_valid_year=1900,
        current_year=datetime.now(tz=UTC).year,
        definitive_score_threshold=85,
        definitive_score_diff=15,
        console_logger=_logger,
    )

    fixtures: list[dict[str, Any]] = []

    test_cases: list[dict[str, Any]] = [
        {
            "id": "resolve_clear_winner",
            "description": "One year clearly dominates",
            "yearScores": {"1982": [90, 85, 70], "2001": [40, 35]},
            "existingYear": None,
        },
        {
            "id": "resolve_close_scores",
            "description": "Two years with close scores (not definitive)",
            "yearScores": {"1997": [80], "1998": [75]},
            "existingYear": None,
        },
        {
            "id": "resolve_existing_year_boost",
            "description": "Existing year gets boost when close to best",
            "yearScores": {"1982": [90], "1983": [85]},
            "existingYear": "1983",
        },
        {
            "id": "resolve_single_result",
            "description": "Only one year candidate",
            "yearScores": {"2020": [75]},
            "existingYear": None,
        },
        {
            "id": "resolve_empty",
            "description": "No year scores",
            "yearScores": {},
            "existingYear": None,
        },
        {
            "id": "resolve_many_candidates",
            "description": "Many year candidates with spread scores",
            "yearScores": {
                "1969": [95, 80],
                "1970": [45],
                "1987": [30],
                "2003": [50, 40],
                "2011": [25],
            },
            "existingYear": None,
        },
        {
            "id": "resolve_future_vs_past",
            "description": "Future year vs past year preference",
            "yearScores": {
                str(datetime.now(tz=UTC).year + 1): [85],
                "2020": [80],
            },
            "existingYear": None,
        },
        {
            "id": "resolve_existing_year_far",
            "description": "Existing year too far from best (no boost)",
            "yearScores": {"2020": [90], "1990": [30]},
            "existingYear": "1990",
        },
        {
            "id": "resolve_tie_prefer_earlier",
            "description": "Equal scores prefer earlier year",
            "yearScores": {"2005": [80], "2000": [80]},
            "existingYear": None,
        },
        {
            "id": "resolve_high_confidence",
            "description": "Very high score is definitive",
            "yearScores": {"2015": [95]},
            "existingYear": None,
        },
        {
            "id": "resolve_low_confidence",
            "description": "Low score is not definitive",
            "yearScores": {"2015": [30]},
            "existingYear": None,
        },
        {
            "id": "resolve_multiple_sources_same_year",
            "description": "Multiple sources agree on same year",
            "yearScores": {"2010": [90, 85, 80]},
            "existingYear": None,
        },
        {
            "id": "resolve_reissue_detection",
            "description": "Original year preferred over reissue year",
            "yearScores": {"1973": [85], "2003": [90], "2011": [70]},
            "existingYear": None,
        },
    ]

    for case in test_cases:
        year_scores: defaultdict[str, list[int]] = defaultdict(list)
        for year, scores in case["yearScores"].items():
            year_scores[str(year)] = scores

        best_year, is_definitive, confidence = resolver.select_best_year(
            year_scores=year_scores,
            existing_year=case.get("existingYear"),
        )

        fixtures.append(
            {
                "id": str(case["id"]),
                "description": str(case["description"]),
                "yearScores": dict(case["yearScores"]),
                "existingYear": case.get("existingYear"),
                "expected": {
                    "year": int(best_year) if best_year else None,
                    "isDefinitive": is_definitive,
                    "confidence": confidence,
                },
            }
        )

    return fixtures


# 4. Year Validation Reference Fixtures
def generate_validation_fixtures() -> list[dict[str, Any]]:
    """Generate year consistency/validation fixtures."""
    checker = YearConsistencyChecker(console_logger=_logger)

    fixtures: list[dict[str, Any]] = []

    test_cases: list[dict[str, Any]] = [
        {
            "id": "valid_dominant_clear",
            "description": "Clear dominant year (>50% of tracks)",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
                {"id": "3", "name": "T3", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
                {"id": "4", "name": "T4", "artist": "A", "album": "Al", "year": "2019", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_no_dominant",
            "description": "No clear dominant year (50/50 split)",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "2019", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_all_same",
            "description": "All tracks same year (100% dominance)",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2015", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "2015", "date_added": "2020-06-01 00:00:00"},
                {"id": "3", "name": "T3", "artist": "A", "album": "Al", "year": "2015", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_empty_years",
            "description": "Some tracks have empty/zero years",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "", "date_added": "2020-06-01 00:00:00"},
                {"id": "3", "name": "T3", "artist": "A", "album": "Al", "year": "0", "date_added": "2020-06-01 00:00:00"},
                {"id": "4", "name": "T4", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_no_tracks",
            "description": "Empty track list",
            "tracks": [],
        },
        {
            "id": "valid_all_empty_years",
            "description": "All tracks have empty years",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "0", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_parity_detection",
            "description": "Top two years differ by 1 count (parity)",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
                {"id": "3", "name": "T3", "artist": "A", "album": "Al", "year": "2019", "date_added": "2020-06-01 00:00:00"},
                {"id": "4", "name": "T4", "artist": "A", "album": "Al", "year": "2018", "date_added": "2020-06-01 00:00:00"},
                {"id": "5", "name": "T5", "artist": "A", "album": "Al", "year": "2018", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_suspicious_year",
            "description": "Dominant year suspiciously old compared to date_added",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "1960", "date_added": "2023-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "1960", "date_added": "2023-06-01 00:00:00"},
                {"id": "3", "name": "T3", "artist": "A", "album": "Al", "year": "1960", "date_added": "2023-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_single_track",
            "description": "Single track album",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_large_majority",
            "description": "Large album with 80% same year",
            "tracks": [
                {
                    "id": str(i),
                    "name": f"T{i}",
                    "artist": "A",
                    "album": "Al",
                    "year": "2020" if i <= 8 else "2019",
                    "date_added": "2020-06-01 00:00:00",
                }
                for i in range(1, 11)
            ],
        },
        # Consensus release year cases
        {
            "id": "valid_consensus_release_year",
            "description": "All tracks have same release_year",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2020", "release_year": "2018", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "2020", "release_year": "2018", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_no_consensus_release_year",
            "description": "Tracks have different release_years",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2020", "release_year": "2018", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "2020", "release_year": "2019", "date_added": "2020-06-01 00:00:00"},
            ],
        },
        {
            "id": "valid_three_way_split",
            "description": "Three different years, no dominant",
            "tracks": [
                {"id": "1", "name": "T1", "artist": "A", "album": "Al", "year": "2018", "date_added": "2020-06-01 00:00:00"},
                {"id": "2", "name": "T2", "artist": "A", "album": "Al", "year": "2019", "date_added": "2020-06-01 00:00:00"},
                {"id": "3", "name": "T3", "artist": "A", "album": "Al", "year": "2020", "date_added": "2020-06-01 00:00:00"},
            ],
        },
    ]

    for case in test_cases:
        raw_tracks: list[dict[str, Any]] = case["tracks"]
        track_dicts = [td for td in (_build_track_dict(t) for t in raw_tracks) if td is not None]

        dominant = checker.get_dominant_year(track_dicts) if track_dicts else None
        most_common = YearConsistencyChecker.get_most_common_year(track_dicts) if track_dicts else None

        # Consensus release year
        consensus_release = (
            checker.get_consensus_release_year(track_dicts) if hasattr(checker, "get_consensus_release_year") and track_dicts else None
        )

        fixtures.append(
            {
                "id": str(case["id"]),
                "description": str(case["description"]),
                "tracks": [_track_to_fixture(t) for t in raw_tracks],
                "expected": {
                    "dominantYear": int(dominant) if dominant else None,
                    "mostCommonYear": int(most_common) if most_common else None,
                    "consensusReleaseYear": int(consensus_release) if consensus_release else None,
                },
            }
        )

    return fixtures


# 5. Year Fallback Reference Fixtures (Synthetic)
def generate_fallback_fixtures() -> list[dict[str, Any]]:
    """Generate fallback strategy fixtures (synthetic, no Python code needed)."""
    return [
        {
            "id": "fallback_high_confidence_api",
            "description": "High confidence API result should be used",
            "context": {
                "bestYear": 2020,
                "bestScore": 90,
                "isDefinitive": True,
                "existingYear": None,
                "albumType": "normal",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "useAPIYear"},
        },
        {
            "id": "fallback_keep_existing_close",
            "description": "Keep existing year when API year is close",
            "context": {
                "bestYear": 2021,
                "bestScore": 60,
                "isDefinitive": False,
                "existingYear": 2020,
                "albumType": "normal",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "keepExisting"},
        },
        {
            "id": "fallback_no_candidates",
            "description": "No scored releases available",
            "context": {
                "bestYear": None,
                "bestScore": 0,
                "isDefinitive": False,
                "existingYear": 2020,
                "albumType": "normal",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "keepExisting"},
        },
        {
            "id": "fallback_no_candidates_no_existing",
            "description": "No scored releases and no existing year",
            "context": {
                "bestYear": None,
                "bestScore": 0,
                "isDefinitive": False,
                "existingYear": None,
                "albumType": "normal",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "noAction"},
        },
        {
            "id": "fallback_compilation_skip",
            "description": "Compilation album should be marked and skipped",
            "context": {
                "bestYear": 2020,
                "bestScore": 50,
                "isDefinitive": False,
                "existingYear": None,
                "albumType": "compilation",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "markAndSkip"},
        },
        {
            "id": "fallback_reissue_album",
            "description": "Reissue album should be marked and skipped",
            "context": {
                "bestYear": 2020,
                "bestScore": 50,
                "isDefinitive": False,
                "existingYear": None,
                "albumType": "reissue",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "markAndSkip"},
        },
        {
            "id": "fallback_low_confidence_no_existing",
            "description": "Low confidence with no existing year triggers verification",
            "context": {
                "bestYear": 2020,
                "bestScore": 30,
                "isDefinitive": False,
                "existingYear": None,
                "albumType": "normal",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "escalateToVerification"},
        },
        {
            "id": "fallback_max_verification_attempts",
            "description": "Max verification attempts reached, use best available",
            "context": {
                "bestYear": 2020,
                "bestScore": 40,
                "isDefinitive": False,
                "existingYear": None,
                "albumType": "normal",
                "verificationAttempts": 3,
            },
            "expected": {"decision": "useAPIYear"},
        },
        {
            "id": "fallback_definitive_overrides_existing",
            "description": "Definitive API result overrides existing year",
            "context": {
                "bestYear": 2020,
                "bestScore": 95,
                "isDefinitive": True,
                "existingYear": 2018,
                "albumType": "normal",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "useAPIYear"},
        },
        {
            "id": "fallback_large_year_diff_keep_existing",
            "description": "Large year difference with low confidence keeps existing",
            "context": {
                "bestYear": 1990,
                "bestScore": 45,
                "isDefinitive": False,
                "existingYear": 2020,
                "albumType": "normal",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "keepExisting"},
        },
        {
            "id": "fallback_special_album_type",
            "description": "Special album type should be marked and skipped",
            "context": {
                "bestYear": 2020,
                "bestScore": 50,
                "isDefinitive": False,
                "existingYear": None,
                "albumType": "special",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "markAndSkip"},
        },
        {
            "id": "fallback_moderate_confidence_use_api",
            "description": "Moderate confidence with no existing year uses API",
            "context": {
                "bestYear": 2020,
                "bestScore": 70,
                "isDefinitive": False,
                "existingYear": None,
                "albumType": "normal",
                "verificationAttempts": 0,
            },
            "expected": {"decision": "useAPIYear"},
        },
    ]


# Config export (for Swift pythonParityScoringConfig)
def generate_python_config() -> dict[str, Any]:
    """Export Python's exact scoring config values for Swift parity config."""
    config_path = PROJECT_ROOT / "config.yaml"
    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    scoring = config["year_retrieval"]["scoring"]
    return {
        "baseScore": scoring["base_score"],
        "artistExactMatchBonus": scoring["artist_exact_match_bonus"],
        "artistSubstringPenalty": scoring.get("artist_substring_penalty", -20),
        "artistCrossScriptPenalty": scoring.get("artist_cross_script_penalty", -10),
        "artistMismatchPenalty": scoring.get("artist_mismatch_penalty", -60),
        "albumExactMatchBonus": scoring["album_exact_match_bonus"],
        "perfectMatchBonus": scoring["perfect_match_bonus"],
        "albumVariationBonus": scoring["album_variation_bonus"],
        "albumSubstringPenalty": scoring["album_substring_penalty"],
        "albumUnrelatedPenalty": scoring["album_unrelated_penalty"],
        "soundtrackCompensationBonus": scoring.get("soundtrack_compensation_bonus", 75),
        "mbReleaseGroupMatchBonus": scoring["mb_release_group_match_bonus"],
        "typeAlbumBonus": scoring["type_album_bonus"],
        "typeEPSinglePenalty": scoring["type_ep_single_penalty"],
        "typeCompilationLivePenalty": scoring["type_compilation_live_penalty"],
        "statusOfficialBonus": scoring["status_official_bonus"],
        "statusBootlegPenalty": scoring["status_bootleg_penalty"],
        "statusPromoPenalty": scoring["status_promo_penalty"],
        "reissuePenalty": scoring["reissue_penalty"],
        "yearDiffPenaltyScale": scoring["year_diff_penalty_scale"],
        "yearDiffMaxPenalty": scoring["year_diff_max_penalty"],
        "yearBeforeStartPenalty": scoring["year_before_start_penalty"],
        "yearAfterEndPenalty": scoring["year_after_end_penalty"],
        "yearNearStartBonus": scoring["year_near_start_bonus"],
        "countryArtistMatchBonus": scoring["country_artist_match_bonus"],
        "countryMajorMarketBonus": scoring["country_major_market_bonus"],
        "sourceMBBonus": scoring["source_mb_bonus"],
        "sourceDiscogsBonus": scoring["source_discogs_bonus"],
        "sourceITunesBonus": scoring.get("source_itunes_bonus", -10),
        "futureYearPenalty": scoring["future_year_penalty"],
        "currentYearPenalty": scoring.get("current_year_penalty", 0),
    }


# Main
def main() -> None:
    """Parse arguments and generate all fixture files."""
    parser = argparse.ArgumentParser(description="Generate Swift parity test fixtures")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for fixture JSON files",
    )
    args = parser.parse_args()

    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading library snapshot...")
    library = _load_library_snapshot()
    representative = _pick_representative_albums(library)
    print(f"Selected {len(representative)} tracks from {len({t.get('album', '') for t in representative})} albums")

    print("Generating genre fixtures...")
    genre_fixtures = generate_genre_fixtures(representative)
    _write_json(output_dir / "genre_reference.json", genre_fixtures)
    print(f"  -> {len(genre_fixtures)} genre test cases")

    print("Generating scoring fixtures...")
    scoring_fixtures = generate_scoring_fixtures()
    _write_json(output_dir / "year_scoring_reference.json", scoring_fixtures)
    print(f"  -> {len(scoring_fixtures)} scoring test cases")

    print("Generating resolution fixtures...")
    resolution_fixtures = generate_resolution_fixtures()
    _write_json(output_dir / "year_resolution_reference.json", resolution_fixtures)
    print(f"  -> {len(resolution_fixtures)} resolution test cases")

    print("Generating validation fixtures...")
    validation_fixtures = generate_validation_fixtures()
    _write_json(output_dir / "year_validation_reference.json", validation_fixtures)
    print(f"  -> {len(validation_fixtures)} validation test cases")

    print("Generating fallback fixtures...")
    fallback_fixtures = generate_fallback_fixtures()
    _write_json(output_dir / "year_fallback_reference.json", fallback_fixtures)
    print(f"  -> {len(fallback_fixtures)} fallback test cases")

    print("Exporting Python scoring config...")
    python_config = generate_python_config()
    _write_json(output_dir / "python_scoring_config.json", python_config)

    total = len(genre_fixtures) + len(scoring_fixtures) + len(resolution_fixtures) + len(validation_fixtures) + len(fallback_fixtures)
    print(f"\nDone! Generated {total} total test cases in {output_dir}")


def _write_json(path: Path, data: Any) -> None:
    """Write JSON with consistent formatting."""
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  Wrote {path.name} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
