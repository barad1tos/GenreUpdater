"""Tests for YearBatchProcessor error handling during concurrent processing.

Verifies that one album failure doesn't crash the entire batch - the processor
should continue processing remaining albums and report failures gracefully.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.types import TrackDict
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
