"""Tests for YearBatchProcessor prerelease handling configuration.

Tests the three prerelease_handling modes:
- process_editable (default): Update editable tracks, mark album for verification
- skip_all: Skip entire album if ANY track is prerelease
- mark_only: Don't process, just mark for verification
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.factories import create_test_app_config  # sourcery skip: dont-import-test-modules
from tests.unit.core.tracks.conftest import (  # sourcery skip: dont-import-test-modules
    create_test_track,
    create_year_batch_processor,
)

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


def _config_with_prerelease_handling(mode: str) -> AppConfig:
    """Create an AppConfig with a specific prerelease_handling mode."""
    config = create_test_app_config()
    config.year_retrieval.processing.prerelease_handling = mode
    return config


class TestPrereleaseHandlingProcessEditable:
    """Tests for prerelease_handling='process_editable' mode (default)."""

    @pytest.mark.asyncio
    async def test_processes_editable_tracks_in_mixed_album(self, mock_year_determinator: MagicMock) -> None:
        """Test that editable tracks are processed in mixed prerelease/purchased album."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("process_editable"),
        )

        # Mixed album: 1 prerelease + 2 purchased
        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Purchased"),
            create_test_track(track_id="track-c", track_status="Purchased"),
        ]

        updated_tracks: list[Any] = []
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
    async def test_marks_mixed_album_for_verification(self, mock_year_determinator: MagicMock) -> None:
        """Test that mixed album is marked for verification."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("process_editable"),
        )

        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock):
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

        mock_year_determinator.pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = mock_year_determinator.pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["metadata"]["mixed_album"] == "true"

    @pytest.mark.asyncio
    async def test_default_mode_is_process_editable(self, mock_year_determinator: MagicMock) -> None:
        """Test that default behavior (no config override) is process_editable."""
        # Default config — prerelease_handling defaults to "process_editable"
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
        )

        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should process (default = process_editable)
            mock_update.assert_called_once()


class TestPrereleaseHandlingSkipAll:
    """Tests for prerelease_handling='skip_all' mode."""

    @pytest.mark.asyncio
    async def test_skips_album_with_any_prerelease(self, mock_year_determinator: MagicMock) -> None:
        """Test that album is skipped entirely when ANY track is prerelease."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("skip_all"),
        )

        # Mixed album - but in skip_all mode, entire album should be skipped
        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Purchased"),
            create_test_track(track_id="track-c", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should NOT have called update - entire album skipped
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_mark_for_verification(self, mock_year_determinator: MagicMock) -> None:
        """Test that skip_all does not mark album for verification."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("skip_all"),
        )

        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock):
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

        # skip_all should not mark for verification
        mock_year_determinator.pending_verification.mark_for_verification.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_album_without_prerelease(self, mock_year_determinator: MagicMock) -> None:
        """Test that album without prerelease is processed normally."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("skip_all"),
        )

        # All purchased - no prerelease
        tracks = [
            create_test_track(track_id="track-a", track_status="Purchased"),
            create_test_track(track_id="track-b", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should process since no prerelease
            mock_update.assert_called_once()


class TestPrereleaseHandlingMarkOnly:
    """Tests for prerelease_handling='mark_only' mode."""

    @pytest.mark.asyncio
    async def test_marks_but_does_not_process(self, mock_year_determinator: MagicMock) -> None:
        """Test that album is marked for verification but not processed."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("mark_only"),
        )

        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should NOT have called update
            mock_update.assert_not_called()

        # But should have marked for verification
        mock_year_determinator.pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = mock_year_determinator.pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["metadata"]["mode"] == "mark_only"

    @pytest.mark.asyncio
    async def test_processes_album_without_prerelease(self, mock_year_determinator: MagicMock) -> None:
        """Test that album without prerelease is processed normally."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("mark_only"),
        )

        # All purchased - no prerelease
        tracks = [
            create_test_track(track_id="track-a", track_status="Purchased"),
            create_test_track(track_id="track-b", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
            await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Should process since no prerelease
            mock_update.assert_called_once()


class TestAllPrereleaseAlbum:
    """Tests for albums where ALL tracks are prerelease (no editable tracks)."""

    @pytest.mark.asyncio
    async def test_all_prerelease_skips_regardless_of_mode(self, mock_year_determinator: MagicMock) -> None:
        """Test that album with ALL prerelease tracks is skipped in all modes."""
        for mode in ["process_editable", "skip_all", "mark_only"]:
            mock_year_determinator.reset_mock()
            processor = create_year_batch_processor(
                year_determinator=mock_year_determinator,
                config=_config_with_prerelease_handling(mode),
            )

            # All prerelease - no editable tracks
            tracks = [
                create_test_track(track_id="track-a", track_status="Prerelease"),
                create_test_track(track_id="track-b", track_status="Prerelease"),
            ]

            with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock) as mock_update:
                await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

                # Should NOT have called update - no editable tracks
                mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_prerelease_marks_for_verification(self, mock_year_determinator: MagicMock) -> None:
        """Test that all-prerelease album is marked for verification."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("process_editable"),
        )

        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Prerelease"),
        ]

        await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

        mock_year_determinator.pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = mock_year_determinator.pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["metadata"]["all_prerelease"] == "true"


class TestInvalidPrereleaseHandlingConfig:
    """Tests for invalid prerelease_handling configuration values."""

    @pytest.mark.asyncio
    async def test_invalid_mode_defaults_to_process_editable(self, mock_year_determinator: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
        """Test that invalid prerelease_handling mode logs warning and defaults to process_editable."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("invalid_mode"),
        )

        # Mixed album: 1 prerelease + 1 purchased (editable)
        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Purchased"),
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
    async def test_invalid_mode_warning_contains_valid_options(self, mock_year_determinator: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
        """Test that warning message includes valid options for user guidance."""
        processor = create_year_batch_processor(
            year_determinator=mock_year_determinator,
            config=_config_with_prerelease_handling("wrong_value"),
        )

        tracks = [
            create_test_track(track_id="track-a", track_status="Prerelease"),
            create_test_track(track_id="track-b", track_status="Purchased"),
        ]

        with patch.object(processor, "_update_tracks_for_album", new_callable=AsyncMock):
            with caplog.at_level(logging.WARNING):
                await processor._process_single_album("Test Artist", "Test Album", tracks, [], [])

            # Warning should contain valid options
            warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
            assert any("mark_only" in msg and "process_editable" in msg and "skip_all" in msg for msg in warning_messages)
