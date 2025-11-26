"""Tests for artist renamer service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.core.tracks.artist import ArtistRenamer
from src.core.models.track import TrackDict
from tests.mocks.csv_mock import MockLogger
from tests.mocks.track_data import DummyTrackData


class DummyTrackProcessor:
    """Minimal track processor stub for testing artist renames."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update_artist_async(
        self,
        track: TrackDict,
        new_artist_name: str,
        *,
        original_artist: str | None = None,
    ) -> bool:
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
    console_logger = MockLogger()
    error_logger = MockLogger()

    track = DummyTrackData.create(artist="DK Energetyk", track_status="subscription")
    track.__dict__["album_artist"] = "DK Energetyk"

    renamer = ArtistRenamer(
        processor,
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
    console_logger = MockLogger()
    error_logger = MockLogger()

    track = DummyTrackData.create(artist="DK Energetyk", track_status="prerelease")

    renamer = ArtistRenamer(
        processor,
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
    console_logger = MockLogger()
    error_logger = MockLogger()

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    assert renamer.has_mapping is False
