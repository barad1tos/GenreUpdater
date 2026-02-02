"""Tests for YearBatchProcessor error handling during concurrent processing.

Verifies that one album failure doesn't crash the entire batch - the processor
should continue processing remaining albums and report failures gracefully.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.types import TrackDict
from core.tracks.year_batch import YearBatchProcessor
from tests.unit.core.tracks.conftest import create_test_track, create_year_batch_processor

if TYPE_CHECKING:
    from core.models.track_models import ChangeLogEntry


def _make_year_determinator_mock() -> MagicMock:
    """Build a MagicMock year-determinator with all async stubs.

    Identical to the default created inside ``create_year_batch_processor``,
    but returned so tests can override individual methods before injection.
    """
    yd = MagicMock()
    yd.should_skip_album = AsyncMock(return_value=(False, None))
    yd.determine_album_year = AsyncMock(return_value="2020")
    yd.check_prerelease_status = AsyncMock(return_value=False)
    yd.check_suspicious_album = AsyncMock(return_value=False)
    yd.handle_future_years = AsyncMock(return_value=False)
    yd.extract_future_years = MagicMock(return_value=[])
    return yd


@pytest.mark.unit
@pytest.mark.asyncio
class TestBatchProcessingErrorResilience:
    """Tests that batch processing continues after individual album failures."""

    async def test_batch_processing_continues_after_single_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """One album failure should not crash entire batch.

        Given 3 albums where the second one fails:
        - Album 1 should succeed
        - Album 2 should fail but be logged
        - Album 3 should still succeed
        """
        year_determinator = _make_year_determinator_mock()

        # Make the second album's determine_album_year call raise an exception
        call_count = 0

        async def mock_determine_year(
            artist: str,
            album: str,
            tracks: list[TrackDict],
            force: bool = False,
        ) -> str | None:
            """Mock year determination that fails for 'Failing Album'."""
            _ = artist, tracks, force  # Mark as intentionally unused
            nonlocal call_count
            call_count += 1
            if album == "Failing Album":
                raise RuntimeError("Simulated API failure for testing")
            return "2020"

        year_determinator.determine_album_year = AsyncMock(side_effect=mock_determine_year)

        processor = create_year_batch_processor(year_determinator=year_determinator)

        # Create 3 albums: first and third will succeed, second will fail
        album_items: list[tuple[tuple[str, str], list[TrackDict]]] = [
            (("Artist1", "Album 1"), [create_test_track("1", artist="Artist1", album="Album 1")]),
            (("Artist2", "Failing Album"), [create_test_track("2", artist="Artist2", album="Failing Album")]),
            (("Artist3", "Album 3"), [create_test_track("3", artist="Artist3", album="Album 3")]),
        ]

        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        with caplog.at_level(logging.WARNING):
            # This should NOT raise an exception even though Album 2 fails
            await processor._process_batches_concurrently(
                album_items=album_items,
                batch_size=10,
                total_albums=3,
                concurrency_limit=2,
                updated_tracks=updated_tracks,
                changes_log=changes_log,
                force=False,
            )

        # Verify the failure was logged
        assert "Failed to process album" in caplog.text or "Failing Album" in caplog.text

        # All 3 albums should have been attempted
        assert call_count == 3

    async def test_multiple_failures_dont_stop_processing(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Multiple album failures should not prevent successful albums from processing."""
        year_determinator = _make_year_determinator_mock()

        successful_albums: list[str] = []

        async def mock_determine_year(
            artist: str,
            album: str,
            tracks: list[TrackDict],
            force: bool = False,
        ) -> str | None:
            """Mock year determination that fails for albums with 'Fail' in name."""
            _ = artist, tracks, force  # Mark as intentionally unused
            if "Fail" in album:
                raise RuntimeError(f"Simulated failure for {album}")
            successful_albums.append(album)
            return "2020"

        year_determinator.determine_album_year = AsyncMock(side_effect=mock_determine_year)

        processor = create_year_batch_processor(year_determinator=year_determinator)

        # 5 albums: 2 will fail, 3 will succeed
        album_items: list[tuple[tuple[str, str], list[TrackDict]]] = [
            (("A1", "Success 1"), [create_test_track("1", artist="A1", album="Success 1")]),
            (("A2", "Fail 1"), [create_test_track("2", artist="A2", album="Fail 1")]),
            (("A3", "Success 2"), [create_test_track("3", artist="A3", album="Success 2")]),
            (("A4", "Fail 2"), [create_test_track("4", artist="A4", album="Fail 2")]),
            (("A5", "Success 3"), [create_test_track("5", artist="A5", album="Success 3")]),
        ]

        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        with caplog.at_level(logging.WARNING):
            await processor._process_batches_concurrently(
                album_items=album_items,
                batch_size=10,
                total_albums=5,
                concurrency_limit=3,
                updated_tracks=updated_tracks,
                changes_log=changes_log,
                force=False,
            )

        # All 3 successful albums should have been processed
        assert len(successful_albums) == 3
        assert "Success 1" in successful_albums
        assert "Success 2" in successful_albums
        assert "Success 3" in successful_albums

    async def test_exception_details_are_logged(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Exception details should be logged for debugging."""
        year_determinator = _make_year_determinator_mock()
        year_determinator.determine_album_year = AsyncMock(side_effect=ValueError("Specific error message for testing"))

        processor = create_year_batch_processor(year_determinator=year_determinator)

        album_items: list[tuple[tuple[str, str], list[TrackDict]]] = [
            (("Artist", "Album"), [create_test_track("1")]),
        ]

        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        with caplog.at_level(logging.WARNING):
            await processor._process_batches_concurrently(
                album_items=album_items,
                batch_size=10,
                total_albums=1,
                concurrency_limit=1,
                updated_tracks=updated_tracks,
                changes_log=changes_log,
                force=False,
            )

        # The error message should be in the logs
        assert "Specific error message for testing" in caplog.text


# ---------------------------------------------------------------------------
# Task 5: Sequential Processing Error Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestSequentialProcessingErrors:
    """Error behavior in sequential processing mode.

    Unlike concurrent mode (which uses ``asyncio.gather(return_exceptions=True)``),
    sequential mode has no try/except around ``_process_single_album``.
    Exceptions propagate directly to the caller.
    """

    async def test_exception_propagates_to_caller(self) -> None:
        """Exception in sequential mode propagates — processing stops."""
        processor = create_year_batch_processor()
        processor._process_single_album = AsyncMock(
            side_effect=RuntimeError("Simulated sequential failure"),
        )

        album_items: list[tuple[tuple[str, str], list[TrackDict]]] = [
            (("Artist", "Album"), [create_test_track("1")]),
        ]

        with pytest.raises(RuntimeError, match="Simulated sequential failure"):
            await processor._process_batches_sequentially(
                album_items=album_items,
                batch_size=10,
                delay_between_batches=0,
                total_albums=1,
                updated_tracks=[],
                changes_log=[],
            )

    async def test_albums_before_failure_are_processed(self) -> None:
        """Albums processed before the failing one complete normally."""
        processed_albums: list[str] = []

        async def mock_process(
            artist: str,
            album: str,
            album_tracks: list[TrackDict],
            updated_tracks: list[TrackDict],
            changes_log: list[ChangeLogEntry],
            force: bool = False,
        ) -> None:
            if album == "Failing":
                raise RuntimeError("boom")
            processed_albums.append(album)

        processor = create_year_batch_processor()
        processor._process_single_album = AsyncMock(side_effect=mock_process)

        album_items: list[tuple[tuple[str, str], list[TrackDict]]] = [
            (("A1", "First"), [create_test_track("1")]),
            (("A2", "Failing"), [create_test_track("2")]),
            (("A3", "Third"), [create_test_track("3")]),
        ]

        with pytest.raises(RuntimeError, match="boom"):
            await processor._process_batches_sequentially(
                album_items=album_items,
                batch_size=10,
                delay_between_batches=0,
                total_albums=3,
                updated_tracks=[],
                changes_log=[],
            )

        assert processed_albums == ["First"]

    async def test_albums_after_failure_are_not_processed(self) -> None:
        """Albums after the failing one are never reached in sequential mode."""
        call_count = 0

        async def mock_process(
            artist: str,
            album: str,
            album_tracks: list[TrackDict],
            updated_tracks: list[TrackDict],
            changes_log: list[ChangeLogEntry],
            force: bool = False,
        ) -> None:
            nonlocal call_count
            call_count += 1
            if album == "Failing":
                raise RuntimeError("boom")

        processor = create_year_batch_processor()
        processor._process_single_album = AsyncMock(side_effect=mock_process)

        album_items: list[tuple[tuple[str, str], list[TrackDict]]] = [
            (("A1", "First"), [create_test_track("1")]),
            (("A2", "Failing"), [create_test_track("2")]),
            (("A3", "Third"), [create_test_track("3")]),
        ]

        with pytest.raises(RuntimeError):
            await processor._process_batches_sequentially(
                album_items=album_items,
                batch_size=10,
                delay_between_batches=0,
                total_albums=3,
                updated_tracks=[],
                changes_log=[],
            )

        # Only 2 calls: First (ok) + Failing (raises) — Third never reached
        assert call_count == 2


# ---------------------------------------------------------------------------
# Task 6: CancelledError Handling Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestCancelledErrorHandling:
    """CancelledError is silently ignored in concurrent mode (graceful shutdown)."""

    async def test_cancelled_error_not_logged_as_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """asyncio.CancelledError is skipped, not logged as album failure."""
        processor = create_year_batch_processor()
        processor._process_single_album = AsyncMock(
            side_effect=asyncio.CancelledError(),
        )

        album_items: list[tuple[tuple[str, str], list[TrackDict]]] = [
            (("Artist", "Album"), [create_test_track("1")]),
        ]

        with caplog.at_level(logging.WARNING):
            await processor._process_batches_concurrently(
                album_items=album_items,
                batch_size=10,
                total_albums=1,
                concurrency_limit=1,
                updated_tracks=[],
                changes_log=[],
            )

        assert "Failed to process album" not in caplog.text

    async def test_non_cancelled_exceptions_still_logged(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Non-CancelledError exceptions are still logged as failures."""
        processor = create_year_batch_processor()
        processor._process_single_album = AsyncMock(
            side_effect=ValueError("real error"),
        )

        album_items: list[tuple[tuple[str, str], list[TrackDict]]] = [
            (("Artist", "Album"), [create_test_track("1")]),
        ]

        with caplog.at_level(logging.WARNING):
            await processor._process_batches_concurrently(
                album_items=album_items,
                batch_size=10,
                total_albums=1,
                concurrency_limit=1,
                updated_tracks=[],
                changes_log=[],
            )

        assert "Failed to process album" in caplog.text
        assert "real error" in caplog.text


# ---------------------------------------------------------------------------
# Task 6: Config Validation Fallback Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigValidationFallbacks:
    """Config extraction with invalid/edge-case values falls back to defaults."""

    def test_invalid_batch_size_falls_back_to_default(self) -> None:
        """Non-numeric batch_size falls back to 10."""
        batch_size, _, _ = YearBatchProcessor._get_processing_settings(
            {"processing": {"batch_size": "not_a_number"}},
        )
        assert batch_size == 10

    def test_negative_batch_size_clamps_to_one(self) -> None:
        """Negative batch_size is clamped to 1 via max(1, ...)."""
        batch_size, _, _ = YearBatchProcessor._get_processing_settings(
            {"processing": {"batch_size": -5}},
        )
        assert batch_size == 1

    def test_zero_batch_size_clamps_to_one(self) -> None:
        """Zero batch_size is clamped to 1."""
        batch_size, _, _ = YearBatchProcessor._get_processing_settings(
            {"processing": {"batch_size": 0}},
        )
        assert batch_size == 1

    def test_invalid_delay_falls_back_to_default(self) -> None:
        """Non-numeric delay falls back to 60."""
        _, delay, _ = YearBatchProcessor._get_processing_settings(
            {"processing": {"delay_between_batches": "invalid"}},
        )
        assert delay == 60

    def test_negative_delay_clamps_to_zero(self) -> None:
        """Negative delay is clamped to 0 via max(0, ...)."""
        _, delay, _ = YearBatchProcessor._get_processing_settings(
            {"processing": {"delay_between_batches": -10}},
        )
        assert delay == 0

    def test_none_batch_size_falls_back_to_default(self) -> None:
        """None batch_size triggers TypeError → falls back to 10."""
        batch_size, _, _ = YearBatchProcessor._get_processing_settings(
            {"processing": {"batch_size": None}},
        )
        assert batch_size == 10

    def test_none_concurrency_uses_applescript_fallback(self) -> None:
        """When api_concurrency is None, apple_script_concurrency is used."""
        processor = create_year_batch_processor(
            config={"apple_script_concurrency": 3},
        )
        limit = processor._determine_concurrency_limit({"rate_limits": {}})
        assert limit == 3

    def test_invalid_api_concurrency_falls_back_to_applescript(self) -> None:
        """Non-numeric api_concurrency falls back to apple_script_concurrency."""
        processor = create_year_batch_processor(
            config={"apple_script_concurrency": 2},
        )
        limit = processor._determine_concurrency_limit(
            {"rate_limits": {"concurrent_api_calls": "not_a_number"}},
        )
        assert limit == 2

    def test_zero_api_concurrency_uses_applescript(self) -> None:
        """Zero api_concurrency falls back to apple_script_concurrency."""
        processor = create_year_batch_processor(
            config={"apple_script_concurrency": 2},
        )
        limit = processor._determine_concurrency_limit(
            {"rate_limits": {"concurrent_api_calls": 0}},
        )
        assert limit == 2

    def test_adaptive_delay_flag_parsed(self) -> None:
        """Adaptive delay bool is correctly extracted from config."""
        _, _, adaptive = YearBatchProcessor._get_processing_settings(
            {"processing": {"adaptive_delay": True}},
        )
        assert adaptive is True


# ---------------------------------------------------------------------------
# Task 7: Track ID Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTrackIdValidation:
    """Validation of track IDs before bulk update."""

    def test_empty_track_id_filtered_out(self) -> None:
        """Empty string IDs are filtered from the list."""
        processor = create_year_batch_processor()
        result = processor._validate_track_ids(
            ["valid", "", "also_valid"],
            artist="Artist",
            album="Album",
        )
        assert result == ["valid", "also_valid"]

    def test_whitespace_track_id_filtered_out(self) -> None:
        """Whitespace-only IDs are filtered from the list."""
        processor = create_year_batch_processor()
        result = processor._validate_track_ids(
            ["valid", "   ", "also_valid"],
            artist="Artist",
            album="Album",
        )
        assert result == ["valid", "also_valid"]

    def test_all_invalid_ids_returns_empty_and_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """All invalid IDs → empty list + warning logged."""
        processor = create_year_batch_processor()
        with caplog.at_level(logging.WARNING):
            result = processor._validate_track_ids(
                ["", "  ", ""],
                artist="Artist",
                album="Album",
            )
        assert result == []
        assert "Filtered out" in caplog.text

    def test_empty_list_returns_empty(self) -> None:
        """Empty input list returns empty without logging."""
        processor = create_year_batch_processor()
        result = processor._validate_track_ids([], artist="Artist", album="Album")
        assert result == []

    def test_all_valid_ids_returned_unchanged(self) -> None:
        """Valid IDs pass through without modification."""
        processor = create_year_batch_processor()
        result = processor._validate_track_ids(
            ["1", "2", "3"],
            artist="Artist",
            album="Album",
        )
        assert result == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# Task 7: Bulk Update Mixed Results Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBulkUpdateMixedResults:
    """Bulk update with mixed success/failure results."""

    async def test_mixed_success_and_failure_counts(self) -> None:
        """Mix of True/False returns counted correctly."""
        processor = create_year_batch_processor()
        processor._update_track_with_retry = AsyncMock(
            side_effect=[True, False, True],
        )

        tracks = [
            create_test_track("1", name="T1"),
            create_test_track("2", name="T2"),
            create_test_track("3", name="T3"),
        ]

        successful, failed = await processor.update_album_tracks_bulk_async(
            tracks=tracks, year="2020", artist="A", album="B",
        )

        assert successful == 2
        assert failed == 1

    async def test_exception_in_gather_counted_as_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Exception from a track update is counted as a failure."""
        processor = create_year_batch_processor()
        processor._update_track_with_retry = AsyncMock(
            side_effect=RuntimeError("unexpected error"),
        )

        tracks = [create_test_track("1", name="T1")]

        with caplog.at_level(logging.ERROR):
            successful, failed = await processor.update_album_tracks_bulk_async(
                tracks=tracks, year="2020", artist="A", album="B",
            )

        assert successful == 0
        assert failed == 1

    async def test_all_succeed_returns_full_count(self) -> None:
        """All successful updates reflected in counts."""
        processor = create_year_batch_processor()
        processor._update_track_with_retry = AsyncMock(return_value=True)

        tracks = [
            create_test_track("1", name="T1"),
            create_test_track("2", name="T2"),
        ]

        successful, failed = await processor.update_album_tracks_bulk_async(
            tracks=tracks, year="2020", artist="A", album="B",
        )

        assert successful == 2
        assert failed == 0

    async def test_no_valid_ids_returns_zero_success(self) -> None:
        """Tracks with no valid IDs return (0, len(tracks))."""
        processor = create_year_batch_processor()

        tracks = [create_test_track("", name="NoID")]

        successful, failed = await processor.update_album_tracks_bulk_async(
            tracks=tracks, year="2020", artist="A", album="B",
        )

        assert successful == 0
        assert failed == 1


# ---------------------------------------------------------------------------
# Task 7: Retry Exhaustion Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestRetryExhaustion:
    """Behavior when all retry attempts are exhausted."""

    async def test_retry_exhaustion_returns_false(self) -> None:
        """Exhausted retries return False (not raise)."""
        retry_handler = MagicMock()
        retry_handler.execute_with_retry = AsyncMock(
            side_effect=OSError("Connection reset"),
        )
        processor = create_year_batch_processor(retry_handler=retry_handler)

        result = await processor._update_track_with_retry(
            track_id="456",
            new_year="2021",
        )

        assert result is False

    async def test_retry_exhaustion_logs_exception(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Exhausted retries log the exception details."""
        retry_handler = MagicMock()
        retry_handler.execute_with_retry = AsyncMock(
            side_effect=RuntimeError("All retries exhausted"),
        )
        processor = create_year_batch_processor(retry_handler=retry_handler)

        with caplog.at_level(logging.ERROR):
            result = await processor._update_track_with_retry(
                track_id="123",
                new_year="2020",
                original_artist="Artist",
                original_album="Album",
            )

        assert result is False
        assert "Failed to update year for track 123" in caplog.text

    async def test_value_error_also_caught(self) -> None:
        """ValueError from retry handler is also caught."""
        retry_handler = MagicMock()
        retry_handler.execute_with_retry = AsyncMock(
            side_effect=ValueError("invalid data"),
        )
        processor = create_year_batch_processor(retry_handler=retry_handler)

        result = await processor._update_track_with_retry(
            track_id="789",
            new_year="2022",
        )

        assert result is False
