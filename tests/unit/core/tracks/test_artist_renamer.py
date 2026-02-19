"""Tests for artist renamer service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from core.models.track_models import TrackDict
from core.tracks.artist_renamer import ArtistRenamer

if TYPE_CHECKING:
    from pathlib import Path


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
    album_artist: str | None = None,
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
        album_artist=album_artist,
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
        update_album_artist: bool = True,
    ) -> bool:
        """Record update call and return success."""
        self.calls.append(
            {
                "track": track,
                "new_artist": new_artist_name,
                "original_artist": original_artist,
                "update_album_artist": update_album_artist,
            }
        )
        return True


@pytest.mark.asyncio
async def test_rename_tracks_updates_artist(tmp_path: Path) -> None:
    """Renamer should update artist and album artist fields when mapping exists."""

    config_path = tmp_path / "artist-renames.yaml"
    config_path.write_text('DK Energetyk: "ДК Енергетик"\n', encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="DK Energetyk", album_artist="DK Energetyk")

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
    assert track.album_artist == "ДК Енергетик"

    assert len(processor.calls) == 1
    call = processor.calls[0]
    assert call["new_artist"] == "ДК Енергетик"
    assert call["original_artist"] == "DK Energetyk"


@pytest.mark.asyncio
async def test_rename_tracks_skips_read_only(tmp_path: Path) -> None:
    """Read-only tracks (e.g., prerelease) should not trigger rename attempts."""

    config_path = tmp_path / "artist-renames.yaml"
    config_path.write_text('DK Energetyk: "ДК Енергетик"\n', encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="DK Energetyk", track_status="prerelease")

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
    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    assert renamer.has_mapping is False


def test_invalid_yaml_syntax(tmp_path: Path) -> None:
    """Invalid YAML syntax should result in empty mapping with error log."""
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("invalid: yaml: syntax: [unclosed", encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    assert renamer.has_mapping is False
    error_logger.exception.assert_called_once()


def test_non_dict_yaml(tmp_path: Path) -> None:
    """YAML that's not a dict should result in empty mapping with warning."""
    config_path = tmp_path / "list.yaml"
    config_path.write_text("- item1\n- item2\n", encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    assert renamer.has_mapping is False
    error_logger.warning.assert_called_once()


def test_non_string_key_value_skipped(tmp_path: Path) -> None:
    """Non-string keys or values should be skipped with warning."""
    config_path = tmp_path / "mixed.yaml"
    # 123 as key and true as value are not strings
    config_path.write_text(
        '123: "Number Key"\nOther: true\nValid: "ValidValue"\n',
        encoding="utf-8",
    )

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    # Should have only the valid entry
    assert renamer.has_mapping is True
    # Two warnings for the invalid entries
    assert error_logger.warning.call_count == 2


def test_empty_key_value_skipped(tmp_path: Path) -> None:
    """Empty keys or values should be skipped with debug log."""
    config_path = tmp_path / "empty.yaml"
    config_path.write_text('"": "EmptyKey"\nEmptyValue: ""\nValid: "ValidValue"\n', encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    # Should have only the valid entry
    assert renamer.has_mapping is True
    # Debug log for the empty entries
    assert error_logger.debug.call_count >= 2


@pytest.mark.asyncio
async def test_empty_mapping_returns_empty(tmp_path: Path) -> None:
    """When mapping is empty, rename_tracks should return empty list."""
    config_path = tmp_path / "empty.yaml"
    config_path.write_text("{}", encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="Some Artist")

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    result = await renamer.rename_tracks([track])
    assert result == []


@pytest.mark.asyncio
async def test_track_without_id_skipped(tmp_path: Path) -> None:
    """Track without ID should be skipped with warning."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text('OldArtist: "NewArtist"\n', encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    # Track with empty ID
    track = _create_track(track_id="", artist="OldArtist")

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    result = await renamer.rename_tracks([track])
    assert result == []
    error_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_empty_artist_skipped(tmp_path: Path) -> None:
    """Track with empty artist should be skipped."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text('OldArtist: "NewArtist"\n', encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="")

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    result = await renamer.rename_tracks([track])
    assert result == []


@pytest.mark.asyncio
async def test_processor_failure_returns_false(tmp_path: Path) -> None:
    """ """
    config_path = tmp_path / "config.yaml"
    config_path.write_text('OldArtist: "NewArtist"\n', encoding="utf-8")

    # Processor that returns False
    class FailingProcessor:
        """Stub processor that always returns False."""

        @staticmethod
        async def update_artist_async(*_args: Any, **_kwargs: Any) -> bool:
            """Always return False."""
            return False

    processor: Any = FailingProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="OldArtist")

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    result = await renamer.rename_tracks([track])
    assert result == []


@pytest.mark.asyncio
async def test_processor_exception_logged(tmp_path: Path) -> None:
    """When processor raises exception, error should be logged."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text('OldArtist: "NewArtist"\n', encoding="utf-8")

    # Processor that raises exception
    class ExceptionProcessor:
        """Stub processor that raises on every call."""

        async def update_artist_async(self, *_args: Any, **_kwargs: Any) -> bool:
            """Always raise exception."""
            raise RuntimeError("Processor failed")

    processor: Any = ExceptionProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="OldArtist")

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    result = await renamer.rename_tracks([track])
    assert result == []
    error_logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_same_normalized_name_skipped(tmp_path: Path) -> None:
    """If new name normalizes to same as old, skip rename."""
    config_path = tmp_path / "config.yaml"
    # Mapping old to same name with different case
    config_path.write_text('TestArtist: "TESTARTIST"\n', encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="TestArtist")

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    result = await renamer.rename_tracks([track])
    # Should skip because normalized names are the same
    assert result == []


@pytest.mark.asyncio
async def test_album_artist_updated_when_matching(tmp_path: Path) -> None:
    """Album artist should be updated when it matches the old artist name."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text('OldArtist: "NewArtist"\n', encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="OldArtist")
    track.album_artist = "OldArtist"

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    result = await renamer.rename_tracks([track])
    assert len(result) == 1
    assert track.artist == "NewArtist"
    assert track.album_artist == "NewArtist"


@pytest.mark.asyncio
async def test_album_artist_not_updated_when_different(tmp_path: Path) -> None:
    """Album artist should NOT be updated when it differs from old artist."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text('OldArtist: "NewArtist"\n', encoding="utf-8")

    processor: Any = DummyTrackProcessor()
    console_logger = MagicMock()
    error_logger = MagicMock()

    track = _create_track(artist="OldArtist")
    track.album_artist = "DifferentAlbumArtist"

    renamer = ArtistRenamer(
        processor,
        console_logger,
        error_logger,
        config_path=config_path,
    )

    result = await renamer.rename_tracks([track])
    assert len(result) == 1
    assert track.artist == "NewArtist"
    # Album artist should remain unchanged
    assert track.album_artist == "DifferentAlbumArtist"
