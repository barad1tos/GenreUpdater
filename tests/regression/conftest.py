"""Regression test fixtures using real library snapshot."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from core.models.track_models import TrackDict

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SNAPSHOT_FILE = FIXTURES_DIR / "library_snapshot.json"


# Minimum tracks to consider snapshot "production-sized"
# Below this, baseline comparisons are meaningless (test fixtures only)
MIN_PRODUCTION_TRACKS = 1000


@pytest.fixture(scope="session")
def library_tracks() -> list[TrackDict]:
    """Load real library snapshot (30K+ tracks) as TrackDict models.

    This fixture loads the full library snapshot that is automatically
    synced from production via the daemon post-run hook.
    JSON dicts are converted to TrackDict Pydantic models for type safety.
    """
    if not SNAPSHOT_FILE.exists():
        pytest.skip(f"Library snapshot not found: {SNAPSHOT_FILE}")

    with SNAPSHOT_FILE.open(encoding="utf-8") as f:
        raw_data = json.load(f)

    return [TrackDict.model_validate(track) for track in raw_data]


@pytest.fixture(scope="session")
def production_sized_snapshot(library_tracks: list[TrackDict]) -> list[TrackDict]:
    """Require production-sized snapshot for meaningful baseline validation.

    Tests comparing against production baselines (track count, artist count, etc.)
    need real data to be meaningful. With test fixtures (<1000 tracks), these
    comparisons would always fail - that's not a bug, it's missing data.

    Skip gracefully with actionable message instead of failing.
    """
    count = len(library_tracks)
    if count < MIN_PRODUCTION_TRACKS:
        pytest.skip(
            f"Test requires production-sized snapshot (>{MIN_PRODUCTION_TRACKS} tracks), got {count}. This is expected in CI with test fixtures."
        )
    return library_tracks


@pytest.fixture(scope="session")
def artists_with_tracks(
    library_tracks: list[TrackDict],
) -> dict[str, list[TrackDict]]:
    """Group tracks by artist."""
    artists: dict[str, list[TrackDict]] = {}
    for track in library_tracks:
        if artist := track.artist:
            artists.setdefault(artist, []).append(track)
    return artists


@pytest.fixture(scope="session")
def albums_with_tracks(
    library_tracks: list[TrackDict],
) -> dict[tuple[str, str], list[TrackDict]]:
    """Group tracks by (artist, album) tuple."""
    albums: dict[tuple[str, str], list[TrackDict]] = {}
    for track in library_tracks:
        artist = track.artist
        album = track.album
        if artist and album:
            albums.setdefault((artist, album), []).append(track)
    return albums


@pytest.fixture(scope="session")
def production_artists(
    production_sized_snapshot: list[TrackDict],
) -> dict[str, list[TrackDict]]:
    """Group tracks by artist (requires production-sized snapshot)."""
    artists: dict[str, list[TrackDict]] = {}
    for track in production_sized_snapshot:
        if artist := track.artist:
            artists.setdefault(artist, []).append(track)
    return artists


@pytest.fixture(scope="session")
def production_albums(
    production_sized_snapshot: list[TrackDict],
) -> dict[tuple[str, str], list[TrackDict]]:
    """Group tracks by (artist, album) tuple (requires production-sized snapshot)."""
    albums: dict[tuple[str, str], list[TrackDict]] = {}
    for track in production_sized_snapshot:
        artist = track.artist
        album = track.album
        if artist and album:
            albums.setdefault((artist, album), []).append(track)
    return albums


@pytest.fixture
def error_logger() -> logging.Logger:
    """Logger for error output in tests."""
    return logging.getLogger("regression_tests")


@pytest.fixture
def console_logger() -> logging.Logger:
    """Logger for console output in tests."""
    logger = logging.getLogger("regression_console")
    logger.setLevel(logging.WARNING)  # Suppress info during tests
    return logger


def pytest_collection_modifyitems(
    items: list[pytest.Item],
) -> None:
    """Add regression marker to all tests in this directory."""
    for item in items:
        if "regression" in str(item.fspath):
            item.add_marker(pytest.mark.regression)
