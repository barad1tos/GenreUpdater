"""Tests for YearBatchProcessor prerelease handling configuration.

Tests the three prerelease_handling modes:
- process_editable (default): Update editable tracks, mark album for verification
- skip_all: Skip entire album if ANY track is prerelease
- mark_only: Don't process, just mark for verification
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.types import TrackDict
from core.tracks.year_batch import YearBatchProcessor


def _create_track(
    track_id: str = "1",
    artist: str = "Test Artist",
    album: str = "Test Album",
    year: str | None = None,
    track_status: str | None = None,
) -> TrackDict:
    """Create a TrackDict for testing."""
    return TrackDict(
        id=track_id,
        name=f"Track {track_id}",
        artist=artist,
        album=album,
        genre="Rock",
        year=year,
        track_status=track_status,
    )


def _create_mock_track_processor() -> MagicMock:
    """Create mock TrackProcessor."""
    mock = MagicMock()
    mock.update_property = AsyncMock(return_value=(True, True))
    return mock


def _create_mock_year_determinator() -> MagicMock:
    """Create mock YearDeterminator with pending_verification."""
    mock = MagicMock()
    mock.pending_verification = MagicMock()
    mock.pending_verification.mark_for_verification = AsyncMock()
    mock.prerelease_recheck_days = 30
    mock.check_suspicious_album = AsyncMock(return_value=False)
    mock.should_skip_album = AsyncMock(return_value=(False, ""))
    mock.determine_album_year = AsyncMock(return_value=2020)
    return mock


def _create_mock_retry_handler() -> MagicMock:
    """Create mock retry handler."""
    return MagicMock()


def _create_mock_analytics() -> MagicMock:
    """Create mock analytics."""
    return MagicMock()


def _create_year_batch_processor(
    track_processor: MagicMock | None = None,
    year_determinator: MagicMock | None = None,
    retry_handler: MagicMock | None = None,
    analytics: MagicMock | None = None,
    config: dict[str, Any] | None = None,
) -> YearBatchProcessor:
    """Create YearBatchProcessor with mock dependencies."""
    return YearBatchProcessor(
        track_processor=track_processor or _create_mock_track_processor(),
        year_determinator=year_determinator or _create_mock_year_determinator(),
        retry_handler=retry_handler or _create_mock_retry_handler(),
        console_logger=logging.getLogger("test.console"),
        error_logger=logging.getLogger("test.error"),
        config=config or {},
        analytics=analytics or _create_mock_analytics(),
    )


class TestPrereleaseHandlingProcessEditable:
    """Tests for prerelease_handling='process_editable' mode (default)."""

    @pytest.mark.asyncio
    async def test_processes_editable_tracks_in_mixed_album(self) -> None:
        """Test that editable tracks are processed in mixed prerelease/purchased album."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "process_editable"}}},
        )

        # Mixed album: 1 prerelease + 2 purchased
        tracks = [
            _create_track("1", track_status="Prerelease"),  # prerelease
            _create_track("2", track_status="Purchased"),  # editable
            _create_track("3", track_status="Purchased"),  # editable
        ]

        updated_tracks: list[TrackDict] = []
        changes_log: list[Any] = []

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, updated_tracks, changes_log)

            # Should have called update for editable tracks
            mock_update.assert_called_once()
            # Verify only 2 editable tracks were passed (not the prerelease)
            call_args = mock_update.call_args
            album_tracks_passed = call_args[0][2]
            assert len(album_tracks_passed) == 2

    @pytest.mark.asyncio
    async def test_marks_mixed_album_for_verification(self) -> None:
        """Test that mixed album is marked for verification."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "process_editable"}}},
        )

        tracks = [
            _create_track("1", track_status="Prerelease"),
            _create_track("2", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock):
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

        year_determinator.pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = year_determinator.pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["metadata"]["mixed_album"] == "true"

    @pytest.mark.asyncio
    async def test_default_mode_is_process_editable(self) -> None:
        """Test that default behavior (no config) is process_editable."""
        year_determinator = _create_mock_year_determinator()
        # No prerelease_handling in config
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={},
        )

        tracks = [
            _create_track("1", track_status="Prerelease"),
            _create_track("2", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should process (default = process_editable)
            mock_update.assert_called_once()


class TestPrereleaseHandlingSkipAll:
    """Tests for prerelease_handling='skip_all' mode."""

    @pytest.mark.asyncio
    async def test_skips_album_with_any_prerelease(self) -> None:
        """Test that album is skipped entirely when ANY track is prerelease."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "skip_all"}}},
        )

        # Mixed album - but in skip_all mode, entire album should be skipped
        tracks = [
            _create_track("1", track_status="Prerelease"),
            _create_track("2", track_status="Purchased"),
            _create_track("3", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should NOT have called update - entire album skipped
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_mark_for_verification(self) -> None:
        """Test that skip_all does not mark album for verification."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "skip_all"}}},
        )

        tracks = [
            _create_track("1", track_status="Prerelease"),
            _create_track("2", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock):
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

        # skip_all should not mark for verification
        year_determinator.pending_verification.mark_for_verification.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_album_without_prerelease(self) -> None:
        """Test that album without prerelease is processed normally."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "skip_all"}}},
        )

        # All purchased - no prerelease
        tracks = [
            _create_track("1", track_status="Purchased"),
            _create_track("2", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should process since no prerelease
            mock_update.assert_called_once()


class TestPrereleaseHandlingMarkOnly:
    """Tests for prerelease_handling='mark_only' mode."""

    @pytest.mark.asyncio
    async def test_marks_but_does_not_process(self) -> None:
        """Test that album is marked for verification but not processed."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "mark_only"}}},
        )

        tracks = [
            _create_track("1", track_status="Prerelease"),
            _create_track("2", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should NOT have called update
            mock_update.assert_not_called()

        # But should have marked for verification
        year_determinator.pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = year_determinator.pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["metadata"]["mode"] == "mark_only"

    @pytest.mark.asyncio
    async def test_processes_album_without_prerelease(self) -> None:
        """Test that album without prerelease is processed normally."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "mark_only"}}},
        )

        # All purchased - no prerelease
        tracks = [
            _create_track("1", track_status="Purchased"),
            _create_track("2", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should process since no prerelease
            mock_update.assert_called_once()


class TestAllPrereleaseAlbum:
    """Tests for albums where ALL tracks are prerelease (no editable tracks)."""

    @pytest.mark.asyncio
    async def test_all_prerelease_skips_regardless_of_mode(self) -> None:
        """Test that album with ALL prerelease tracks is skipped in all modes."""
        for mode in ["process_editable", "skip_all", "mark_only"]:
            year_determinator = _create_mock_year_determinator()
            processor = _create_year_batch_processor(
                year_determinator=year_determinator,
                config={"year_retrieval": {"processing": {"prerelease_handling": mode}}},
            )

            # All prerelease - no editable tracks
            tracks = [
                _create_track("1", track_status="Prerelease"),
                _create_track("2", track_status="Prerelease"),
            ]

            with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
                await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

                # Should NOT have called update - no editable tracks
                mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_prerelease_marks_for_verification(self) -> None:
        """Test that all-prerelease album is marked for verification."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "process_editable"}}},
        )

        tracks = [
            _create_track("1", track_status="Prerelease"),
            _create_track("2", track_status="Prerelease"),
        ]

        await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

        year_determinator.pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = year_determinator.pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["metadata"]["all_prerelease"] == "true"


class TestInvalidPrereleaseHandlingConfig:
    """Tests for invalid prerelease_handling configuration values."""

    @pytest.mark.asyncio
    async def test_invalid_mode_defaults_to_process_editable(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that invalid prerelease_handling mode logs warning and defaults to process_editable."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "invalid_mode"}}},
        )

        # Mixed album: 1 prerelease + 1 purchased (editable)
        tracks = [
            _create_track("1", track_status="Prerelease"),
            _create_track("2", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            with caplog.at_level(logging.WARNING):
                await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should have logged warning about invalid mode
            assert any("Unknown prerelease_handling mode" in record.message for record in caplog.records)
            assert any("invalid_mode" in record.message for record in caplog.records)

            # Should still process (defaulted to process_editable behavior)
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_mode_warning_contains_valid_options(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that warning message includes valid options for user guidance."""
        year_determinator = _create_mock_year_determinator()
        processor = _create_year_batch_processor(
            year_determinator=year_determinator,
            config={"year_retrieval": {"processing": {"prerelease_handling": "wrong_value"}}},
        )

        tracks = [
            _create_track("1", track_status="Prerelease"),
            _create_track("2", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock):
            with caplog.at_level(logging.WARNING):
                await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Warning should contain valid options
            warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
            assert any("mark_only" in msg and "process_editable" in msg and "skip_all" in msg for msg in warning_messages)
