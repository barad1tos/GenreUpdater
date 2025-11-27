"""Tests for artist renamer service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

from src.core.models.track_models import TrackDict
from src.core.tracks.artist_renamer import ArtistRenamer

if TYPE_CHECKING:
    from pathlib import Path

    from src.core.tracks.track_processor import TrackProcessor


def _create_track(
    track_id: str = "12345",
    name: str = "Test Track",
    artist: str = "Test Artist",
    album: str = "Test Album",
    genre: str | None = "Rock",
    date_added: str | None = "2024-01-01 12:00:00",
    year: str | None = "2024",
    last_modified: str | None = "2024-01-01 12:00:00",
    track_status: str | None = "subscription",
) -> TrackDict:
    """Create a TrackDict for testing."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        genre=genre,
        date_added=date_added,
        year=year,
        last_modified=last_modified,
        track_status=track_status,
    )


class DummyTrackProcessor:
    """Minimal track processor stub for testing artist renames."""

    def __init__(self) -> None:
        """Initialize calls tracker."""
        self.calls: list[dict[str, Any]] = []

    async def update_artist_async(
        self,
        track: TrackDict,
        new_artist_name: str,
        *,
        original_artist: str | None = None,
    ) -> bool:
        """Record update call and return success."""
        self.calls.append(
            {
                "track": track,
                "new_artist": new_artist_name,
                "original_artist": original_artist,
            }
        )
        return True


@pytest.mark.asyncio
async def test_rename_tracks_updates_artist(tmp_path: Path) -> None:
    """Renamer should update artist and album artist fields when mapping exists."""

    config_path = tmp_path / "artist-renames.yaml"
    config_path.write_text('DK Energetyk: "ДК Енергетик"\n', encoding="utf-8")

    processor = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="DK Energetyk")
    track.__dict__["album_artist"] = "DK Energetyk"

    renamer = ArtistRenamer(
        cast("TrackProcessor", processor),
        console_logger,
        error_logger,
        config_path=config_path,
    )

    updated_tracks = await renamer.rename_tracks([track])

    assert len(updated_tracks) == 1
    assert track.artist == "ДК Енергетик"
    assert track.original_artist == "DK Energetyk"
    assert track.__dict__["album_artist"] == "ДК Енергетик"

    assert len(processor.calls) == 1
    call = processor.calls[0]
    assert call["new_artist"] == "ДК Енергетик"
    assert call["original_artist"] == "DK Energetyk"


@pytest.mark.asyncio
async def test_rename_tracks_skips_read_only(tmp_path: Path) -> None:
    """Read-only tracks (e.g., prerelease) should not trigger rename attempts."""

    config_path = tmp_path / "artist-renames.yaml"
    config_path.write_text('DK Energetyk: "ДК Енергетик"\n', encoding="utf-8")

    processor = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="DK Energetyk", track_status="prerelease")

    renamer = ArtistRenamer(
        cast("TrackProcessor", processor),
        console_logger,
        error_logger,
        config_path=config_path,
    )

    updated_tracks = await renamer.rename_tracks([track])

    assert updated_tracks == []
    assert track.artist == "DK Energetyk"
    assert processor.calls == []


def test_missing_config_yields_no_mapping(tmp_path: Path) -> None:
    """Missing config file should result in empty mapping without errors."""

    config_path = tmp_path / "missing.yaml"
    processor = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    renamer = ArtistRenamer(
        cast("TrackProcessor", processor),
        console_logger,
        error_logger,
        config_path=config_path,
    )

    assert renamer.has_mapping is False
