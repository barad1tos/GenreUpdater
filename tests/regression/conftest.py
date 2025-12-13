"""Regression test fixtures using real library snapshot."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from core.models.track import TrackDict

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SNAPSHOT_FILE = FIXTURES_DIR / "library_snapshot.json"


@pytest.fixture(scope="session")
def library_tracks() -> list[TrackDict]:
    """Load real library snapshot (30K+ tracks).

    This fixture loads the full library snapshot that is automatically
    synced from production via the daemon post-run hook.
    """
    if not SNAPSHOT_FILE.exists():
        pytest.skip(f"Library snapshot not found: {SNAPSHOT_FILE}")

    with SNAPSHOT_FILE.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def artists_with_tracks(
    library_tracks: list[TrackDict],
) -> dict[str, list[TrackDict]]:
    """Group tracks by artist."""
    artists: dict[str, list[TrackDict]] = {}
    for track in library_tracks:
        artist = track.get("artist", "")
        if artist:
            artists.setdefault(artist, []).append(track)
    return artists


@pytest.fixture(scope="session")
def albums_with_tracks(
    library_tracks: list[TrackDict],
) -> dict[tuple[str, str], list[TrackDict]]:
    """Group tracks by (artist, album) tuple."""
    albums: dict[tuple[str, str], list[TrackDict]] = {}
    for track in library_tracks:
        artist = track.get("artist", "")
        album = track.get("album", "")
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


@pytest.fixture(scope="session")
def unique_artists(artists_with_tracks: dict[str, list[TrackDict]]) -> list[str]:
    """List of unique artist names."""
    return list(artists_with_tracks.keys())


@pytest.fixture(scope="session")
def unique_albums(
    albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
) -> list[tuple[str, str]]:
    """List of unique (artist, album) tuples."""
    return list(albums_with_tracks.keys())


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Add regression marker to all tests in this directory."""
    for item in items:
        if "regression" in str(item.fspath):
            item.add_marker(pytest.mark.regression)
