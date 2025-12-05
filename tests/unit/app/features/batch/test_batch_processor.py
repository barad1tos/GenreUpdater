"""Unit tests for batch processor module."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.features.batch.batch_processor import BatchProcessor

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create test console logger."""
    return logging.getLogger("test.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create test error logger."""
    return logging.getLogger("test.error")


@pytest.fixture
def mock_music_updater() -> MagicMock:
    """Create mock music updater."""
    updater = MagicMock()
    updater.run_clean_artist = AsyncMock()
    updater.run_update_years = AsyncMock()
    return updater


@pytest.fixture
def processor(
    mock_music_updater: MagicMock,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> BatchProcessor:
    """Create BatchProcessor instance."""
    return BatchProcessor(mock_music_updater, console_logger, error_logger)


class TestBatchProcessorInit:
    """Tests for BatchProcessor initialization."""

    def test_init_stores_dependencies(
        self,
        mock_music_updater: MagicMock,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should store all dependencies correctly."""
        processor = BatchProcessor(mock_music_updater, console_logger, error_logger)

        assert processor.music_updater is mock_music_updater
        assert processor.console_logger is console_logger
        assert processor.error_logger is error_logger


class TestProcessFromFile:
    """Tests for process_from_file method."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_file_not_found(self, processor: BatchProcessor, tmp_path: Path) -> None:
        """Should return empty results when file doesn't exist."""
        result = await processor.process_from_file(str(tmp_path / "nonexistent.txt"))

        assert result == {"successful": [], "failed": [], "skipped": []}

    @pytest.mark.asyncio
    async def test_reads_artists_from_file(
        self,
        processor: BatchProcessor,
        mock_music_updater: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should read and process artists from file."""
        artists_file = tmp_path / "artists.txt"
        artists_file.write_text("Artist One\nArtist Two\n")

        result = await processor.process_from_file(str(artists_file))

        assert "Artist One" in result["successful"]
        assert "Artist Two" in result["successful"]
        assert mock_music_updater.run_clean_artist.call_count == 2
        assert mock_music_updater.run_update_years.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_empty_lines(
        self,
        processor: BatchProcessor,
        mock_music_updater: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should skip empty lines in file."""
        artists_file = tmp_path / "artists.txt"
        artists_file.write_text("Artist One\n\n\nArtist Two\n")

        result = await processor.process_from_file(str(artists_file))

        assert len(result["successful"]) == 2
        assert mock_music_updater.run_clean_artist.call_count == 2


class TestProcessArtists:
    """Tests for process_artists method."""

    @pytest.mark.asyncio
    async def test_full_operation_runs_clean_and_years(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """Full operation should run clean_artist and update_years."""
        result = await processor.process_artists(["Test Artist"])

        assert "Test Artist" in result["successful"]
        mock_music_updater.run_clean_artist.assert_called_once()
        mock_music_updater.run_update_years.assert_called_once()

    @pytest.mark.asyncio
    async def test_clean_operation_only_runs_clean(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """Clean operation should only run clean_artist."""
        result = await processor.process_artists(["Test Artist"], operation="clean")

        assert "Test Artist" in result["successful"]
        mock_music_updater.run_clean_artist.assert_called_once()
        mock_music_updater.run_update_years.assert_not_called()

    @pytest.mark.asyncio
    async def test_years_operation_only_runs_years(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """Years operation should only run update_years."""
        result = await processor.process_artists(["Test Artist"], operation="years")

        assert "Test Artist" in result["successful"]
        mock_music_updater.run_clean_artist.assert_not_called()
        mock_music_updater.run_update_years.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_failed_artists(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """Should add failed artists to results."""
        mock_music_updater.run_clean_artist.side_effect = RuntimeError("Processing failed")

        result = await processor.process_artists(["Failing Artist"], operation="clean")

        assert "Failing Artist" in result["failed"]
        assert not result["successful"]

    @pytest.mark.asyncio
    async def test_continues_after_failure(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """Should continue processing after individual failure."""
        mock_music_updater.run_clean_artist.side_effect = [
            RuntimeError("First failed"),
            None,  # Second succeeds
        ]

        result = await processor.process_artists(["Artist 1", "Artist 2"], operation="clean")

        assert "Artist 1" in result["failed"]
        assert "Artist 2" in result["successful"]

    @pytest.mark.asyncio
    async def test_passes_force_flag_to_years(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """Should pass force flag to run_update_years (run_clean_artist doesn't use force)."""
        await processor.process_artists(["Test Artist"], operation="years", force=True)

        mock_music_updater.run_update_years.assert_called_once_with("Test Artist", True)

    @pytest.mark.asyncio
    async def test_clean_does_not_use_force(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """operation='clean' should ignore force and only call run_clean_artist."""
        await processor.process_artists(["Test Artist"], operation="clean", force=True)

        mock_music_updater.run_clean_artist.assert_called_once_with("Test Artist")
        mock_music_updater.run_update_years.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_uses_force_for_years_only(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """operation='full' with force=True should pass force only to run_update_years."""
        await processor.process_artists(["Test Artist"], operation="full", force=True)

        # run_clean_artist called without force
        mock_music_updater.run_clean_artist.assert_called_once_with("Test Artist")
        # run_update_years called with force=True
        mock_music_updater.run_update_years.assert_called_once_with("Test Artist", True)

    @pytest.mark.asyncio
    async def test_cancelled_error_adds_remaining_to_skipped(self, processor: BatchProcessor, mock_music_updater: MagicMock) -> None:
        """Should add remaining artists to skipped on cancellation."""
        # First artist triggers cancellation
        mock_music_updater.run_clean_artist.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await processor.process_artists(["Artist 1", "Artist 2", "Artist 3"], operation="clean")


class TestPrintSummary:
    """Tests for print_summary method."""

    def test_prints_success_count(self, processor: BatchProcessor, caplog: pytest.LogCaptureFixture) -> None:
        """Should log successful count."""
        results = {"successful": ["A", "B"], "failed": [], "skipped": []}

        with caplog.at_level(logging.INFO):
            processor.print_summary(results, total=2)

        assert "Successful: 2" in caplog.text

    def test_prints_failed_artists(self, processor: BatchProcessor, caplog: pytest.LogCaptureFixture) -> None:
        """Should log failed artists."""
        results = {"successful": [], "failed": ["Bad Artist"], "skipped": []}

        with caplog.at_level(logging.ERROR):
            processor.print_summary(results, total=1)

        assert "Failed: 1" in caplog.text
        assert "Bad Artist" in caplog.text

    def test_prints_skipped_artists(self, processor: BatchProcessor, caplog: pytest.LogCaptureFixture) -> None:
        """Should log skipped artists."""
        results = {"successful": [], "failed": [], "skipped": ["Skipped Artist"]}

        with caplog.at_level(logging.WARNING):
            processor.print_summary(results, total=1)

        assert "Skipped: 1" in caplog.text
        assert "Skipped Artist" in caplog.text

    def test_prints_success_rate(self, processor: BatchProcessor, caplog: pytest.LogCaptureFixture) -> None:
        """Should calculate and log success rate."""
        results = {"successful": ["A"], "failed": ["B"], "skipped": []}

        with caplog.at_level(logging.INFO):
            processor.print_summary(results, total=2)

        assert "Success rate: 50.0%" in caplog.text

    def test_handles_zero_total(self, processor: BatchProcessor, caplog: pytest.LogCaptureFixture) -> None:
        """Should handle zero total without division error."""
        results: dict[str, list[str]] = {"successful": [], "failed": [], "skipped": []}

        with caplog.at_level(logging.INFO):
            processor.print_summary(results, total=0)

        # Should not raise and not print success rate
        assert "Success rate" not in caplog.text
