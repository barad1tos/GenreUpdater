"""Tests for TrackCleaningService."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.track_cleaning import TrackCleaningService, _normalize_whitespace
from core.models.track_models import ChangeLogEntry, TrackDict


# ========================= Normalize Whitespace Tests =========================


class TestNormalizeWhitespace:
    """Tests for _normalize_whitespace function."""

    def test_collapses_multiple_spaces(self) -> None:
        """Should collapse multiple spaces to single space."""
        assert _normalize_whitespace("hello    world") == "hello world"

    def test_strips_leading_trailing_whitespace(self) -> None:
        """Should strip leading and trailing whitespace."""
        assert _normalize_whitespace("  hello  ") == "hello"

    def test_handles_tabs_and_newlines(self) -> None:
        """Should handle tabs and newlines."""
        assert _normalize_whitespace("hello\t\nworld") == "hello world"

    def test_handles_empty_string(self) -> None:
        """Should handle empty string."""
        assert _normalize_whitespace("") == ""

    def test_handles_whitespace_only(self) -> None:
        """Should handle whitespace-only string."""
        assert _normalize_whitespace("   \t\n   ") == ""

    def test_preserves_single_spaces(self) -> None:
        """Should preserve single spaces."""
        assert _normalize_whitespace("hello world") == "hello world"


# ========================= Fixtures =========================


@pytest.fixture
def mock_track_processor() -> MagicMock:
    """Create mock track processor."""
    processor = MagicMock()
    processor.update_track_async = AsyncMock(return_value=True)
    return processor


@pytest.fixture
def mock_config() -> dict[str, Any]:
    """Create mock config."""
    return {"logs_base_dir": "/tmp/logs"}


@pytest.fixture
def service(
    mock_track_processor: MagicMock,
    mock_config: dict[str, Any],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> TrackCleaningService:
    """Create TrackCleaningService instance."""
    return TrackCleaningService(
        track_processor=mock_track_processor,
        config=mock_config,
        console_logger=console_logger,
        error_logger=error_logger,
    )


@pytest.fixture
def sample_track() -> TrackDict:
    """Create sample track."""
    return TrackDict(
        id="123",
        name="Track Name",
        artist="Artist Name",
        album="Album Name",
        genre="Rock",
        year="2020",
    )


# ========================= Init Tests =========================


class TestTrackCleaningServiceInit:
    """Tests for TrackCleaningService initialization."""

    def test_stores_track_processor(self, service: TrackCleaningService, mock_track_processor: MagicMock) -> None:
        """Should store track processor."""
        assert service._track_processor is mock_track_processor

    def test_stores_config(self, service: TrackCleaningService, mock_config: dict[str, Any]) -> None:
        """Should store config."""
        assert service._config is mock_config

    def test_stores_console_logger(self, service: TrackCleaningService, console_logger: logging.Logger) -> None:
        """Should store console logger."""
        assert service._console_logger is console_logger

    def test_stores_error_logger(self, service: TrackCleaningService, error_logger: logging.Logger) -> None:
        """Should store error logger."""
        assert service._error_logger is error_logger


# ========================= Extract and Clean Metadata Tests =========================


class TestExtractAndCleanMetadata:
    """Tests for extract_and_clean_metadata method."""

    def test_extracts_track_fields(self, service: TrackCleaningService, sample_track: TrackDict) -> None:
        """Should extract track fields."""
        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("Clean Track", "Clean Album")
            result = service.extract_and_clean_metadata(sample_track)

        track_id, artist, track_name, album_name, cleaned_track, cleaned_album = result
        assert track_id == "123"
        assert artist == "Artist Name"
        assert track_name == "Track Name"
        assert album_name == "Album Name"
        assert cleaned_track == "Clean Track"
        assert cleaned_album == "Clean Album"

    def test_handles_missing_fields(self, service: TrackCleaningService) -> None:
        """Should handle missing fields."""
        track = TrackDict(id="1", name="", artist="", album="")

        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("", "")
            result = service.extract_and_clean_metadata(track)

        track_id, artist, track_name, album_name, _, _ = result
        assert track_id == "1"
        assert artist == ""
        assert track_name == ""
        assert album_name == ""

    def test_calls_clean_names_with_correct_args(self, service: TrackCleaningService, sample_track: TrackDict) -> None:
        """Should call clean_names with correct arguments."""
        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("Track", "Album")
            service.extract_and_clean_metadata(sample_track)

        mock_clean.assert_called_once()
        call_kwargs = mock_clean.call_args.kwargs
        assert call_kwargs["artist"] == "Artist Name"
        assert call_kwargs["track_name"] == "Track Name"
        assert call_kwargs["album_name"] == "Album Name"


# ========================= Create Change Log Entry Tests =========================


class TestCreateChangeLogEntry:
    """Tests for _create_change_log_entry static method."""

    def test_creates_entry_with_all_fields(self) -> None:
        """Should create entry with all fields."""
        entry = TrackCleaningService._create_change_log_entry(
            track_id="123",
            artist="Artist",
            original_track_name="Original Track",
            original_album_name="Original Album",
            cleaned_track_name="Clean Track",
            cleaned_album_name="Clean Album",
        )

        assert isinstance(entry, ChangeLogEntry)
        assert entry.change_type == "metadata_cleaning"
        assert entry.track_id == "123"
        assert entry.artist == "Artist"
        assert entry.old_track_name == "Original Track"
        assert entry.new_track_name == "Clean Track"
        assert entry.old_album_name == "Original Album"
        assert entry.new_album_name == "Clean Album"

    def test_sets_timestamp(self) -> None:
        """Should set timestamp."""
        entry = TrackCleaningService._create_change_log_entry(
            track_id="1",
            artist="A",
            original_track_name="T",
            original_album_name="Al",
            cleaned_track_name="T",
            cleaned_album_name="Al",
        )

        assert entry.timestamp is not None
        assert len(entry.timestamp) > 0


# ========================= Process Single Track Tests =========================


class TestProcessSingleTrack:
    """Tests for process_single_track method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_track_id(self, service: TrackCleaningService) -> None:
        """Should return (None, None) when track has no ID."""
        track = TrackDict(id="", name="Track", artist="Artist", album="Album")

        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("Clean Track", "Clean Album")
            result = await service.process_single_track(track)

        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_changes_needed(self, service: TrackCleaningService, sample_track: TrackDict) -> None:
        """Should return (None, None) when no changes needed."""
        with patch("app.track_cleaning.clean_names") as mock_clean:
            # Return same values as original (normalized)
            mock_clean.return_value = ("Track Name", "Album Name")
            result = await service.process_single_track(sample_track)

        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_updates_track_when_changes_needed(
        self,
        service: TrackCleaningService,
        mock_track_processor: MagicMock,
        sample_track: TrackDict,
    ) -> None:
        """Should update track when changes needed."""
        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("Cleaned Track", "Cleaned Album")
            updated_track, change_entry = await service.process_single_track(sample_track)

        assert updated_track is not None
        assert updated_track.name == "Cleaned Track"
        assert updated_track.album == "Cleaned Album"
        assert change_entry is not None
        mock_track_processor.update_track_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_update_fails(
        self,
        service: TrackCleaningService,
        mock_track_processor: MagicMock,
        sample_track: TrackDict,
    ) -> None:
        """Should return (None, None) when update fails."""
        mock_track_processor.update_track_async = AsyncMock(return_value=False)

        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("Cleaned Track", "Cleaned Album")
            result = await service.process_single_track(sample_track)

        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_uses_artist_override(self, service: TrackCleaningService, sample_track: TrackDict) -> None:
        """Should use artist override for change entry."""
        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("Cleaned Track", "Cleaned Album")
            _, change_entry = await service.process_single_track(sample_track, artist_override="Override Artist")

        assert change_entry is not None
        assert change_entry.artist == "Override Artist"

    @pytest.mark.asyncio
    async def test_only_updates_changed_fields(
        self,
        service: TrackCleaningService,
        mock_track_processor: MagicMock,
        sample_track: TrackDict,
    ) -> None:
        """Should only update fields that changed."""
        with patch("app.track_cleaning.clean_names") as mock_clean:
            # Only album changed
            mock_clean.return_value = ("Track Name", "Cleaned Album")
            await service.process_single_track(sample_track)

        call_kwargs = mock_track_processor.update_track_async.call_args.kwargs
        assert call_kwargs["new_track_name"] is None
        assert call_kwargs["new_album_name"] == "Cleaned Album"


# ========================= Process All Tracks Tests =========================


class TestProcessAllTracks:
    """Tests for process_all_tracks method."""

    @pytest.mark.asyncio
    async def test_processes_multiple_tracks(self, service: TrackCleaningService) -> None:
        """Should process multiple tracks."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Artist", album="Album 1"),
            TrackDict(id="2", name="Track 2", artist="Artist", album="Album 2"),
        ]

        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("Cleaned Track", "Cleaned Album")
            updated_tracks, changes_log = await service.process_all_tracks(tracks, artist="Artist")

        assert len(updated_tracks) == 2
        assert len(changes_log) == 2

    @pytest.mark.asyncio
    async def test_handles_empty_list(self, service: TrackCleaningService) -> None:
        """Should handle empty track list."""
        updated_tracks, changes_log = await service.process_all_tracks([], artist="Artist")

        assert updated_tracks == []
        assert changes_log == []

    @pytest.mark.asyncio
    async def test_filters_unchanged_tracks(self, service: TrackCleaningService) -> None:
        """Should not include unchanged tracks in result."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Artist", album="Album 1"),
            TrackDict(id="2", name="Track 2", artist="Artist", album="Album 2"),
        ]

        with patch("app.track_cleaning.clean_names") as mock_clean:
            # First track changes, second doesn't
            mock_clean.side_effect = [
                ("Cleaned Track", "Cleaned Album"),
                ("Track 2", "Album 2"),
            ]
            updated_tracks, changes_log = await service.process_all_tracks(tracks, artist="Artist")

        assert len(updated_tracks) == 1
        assert len(changes_log) == 1


# ========================= Clean All Metadata With Logs Tests =========================


class TestCleanAllMetadataWithLogs:
    """Tests for clean_all_metadata_with_logs method."""

    @pytest.mark.asyncio
    async def test_returns_change_log_entries(self, service: TrackCleaningService) -> None:
        """Should return change log entries."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Artist", album="Album 1"),
        ]

        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.return_value = ("Cleaned Track", "Cleaned Album")
            result = await service.clean_all_metadata_with_logs(tracks)

        assert len(result) == 1
        assert isinstance(result[0], ChangeLogEntry)
        assert result[0].change_type == "metadata_cleaning"

    @pytest.mark.asyncio
    async def test_logs_count_when_tracks_cleaned(
        self,
        service: TrackCleaningService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log count when tracks are cleaned."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Artist", album="Album 1"),
        ]

        with (
            patch("app.track_cleaning.clean_names") as mock_clean,
            caplog.at_level(logging.INFO),
        ):
            mock_clean.return_value = ("Cleaned Track", "Cleaned Album")
            await service.clean_all_metadata_with_logs(tracks)

        assert "Cleaned metadata for 1 tracks" in caplog.text

    @pytest.mark.asyncio
    async def test_handles_empty_list(self, service: TrackCleaningService) -> None:
        """Should handle empty track list."""
        result = await service.clean_all_metadata_with_logs([])
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_unchanged_tracks(self, service: TrackCleaningService) -> None:
        """Should not include unchanged tracks in result."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Artist", album="Album 1"),
            TrackDict(id="2", name="Track 2", artist="Artist", album="Album 2"),
        ]

        with patch("app.track_cleaning.clean_names") as mock_clean:
            mock_clean.side_effect = [
                ("Cleaned Track", "Cleaned Album"),
                ("Track 2", "Album 2"),  # No change
            ]
            result = await service.clean_all_metadata_with_logs(tracks)

        assert len(result) == 1


# ========================= Whitespace Normalization Edge Cases =========================


class TestWhitespaceNormalizationInProcessing:
    """Tests for whitespace normalization during track processing."""

    @pytest.mark.asyncio
    async def test_ignores_whitespace_only_differences(self, service: TrackCleaningService) -> None:
        """Should ignore whitespace-only differences."""
        track = TrackDict(
            id="1",
            name="Track  Name",  # Double space
            artist="Artist",
            album="Album  Name",  # Double space
        )

        with patch("app.track_cleaning.clean_names") as mock_clean:
            # clean_names normalizes whitespace
            mock_clean.return_value = ("Track Name", "Album Name")
            result = await service.process_single_track(track)

        # Should be no change because normalized versions match
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_detects_real_changes_with_whitespace(self, service: TrackCleaningService) -> None:
        """Should detect real changes even with whitespace normalization."""
        track = TrackDict(
            id="1",
            name="Track  Name  (Promo)",  # Has promo text
            artist="Artist",
            album="Album Name",
        )

        with patch("app.track_cleaning.clean_names") as mock_clean:
            # Promo text removed
            mock_clean.return_value = ("Track Name", "Album Name")
            updated, _ = await service.process_single_track(track)

        assert updated is not None
        assert updated.name == "Track Name"
