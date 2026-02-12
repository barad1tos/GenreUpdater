"""Unit tests for run tracking utilities."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from core.run_tracking import IncrementalRunTracker
from tests.factories import MINIMAL_CONFIG_DATA, create_test_app_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    """Create test configuration with temp directory."""
    logging_overrides = {
        **MINIMAL_CONFIG_DATA["logging"],
        "last_incremental_run_file": "last_run.log",
    }
    return create_test_app_config(
        logs_base_dir=str(tmp_path / "logs"),
        logging=logging_overrides,
    )


@pytest.fixture
def tracker(config: AppConfig) -> IncrementalRunTracker:
    """Create an IncrementalRunTracker instance."""
    return IncrementalRunTracker(config)


class TestIncrementalRunTrackerInit:
    """Tests for IncrementalRunTracker initialization."""

    def test_init_stores_config(self, config: AppConfig) -> None:
        """Should store configuration."""
        tracker = IncrementalRunTracker(config)
        assert tracker.config is config


class TestGetLastRunFilePath:
    """Tests for get_last_run_file_path method."""

    def test_returns_path_from_config(self, tracker: IncrementalRunTracker) -> None:
        """Should return path based on config."""
        result = tracker.get_last_run_file_path()
        assert "last_run.log" in result or "last_incremental_run.log" in result


class TestUpdateLastRunTimestamp:
    """Tests for update_last_run_timestamp method."""

    @pytest.mark.asyncio
    async def test_creates_timestamp_file(self, tracker: IncrementalRunTracker) -> None:
        """Should create timestamp file."""
        await tracker.update_last_run_timestamp()

        file_path = Path(tracker.get_last_run_file_path())
        assert file_path.exists()

    @pytest.mark.asyncio
    async def test_writes_iso_format_timestamp(self, tracker: IncrementalRunTracker) -> None:
        """Should write ISO format timestamp."""
        await tracker.update_last_run_timestamp()

        file_path = Path(tracker.get_last_run_file_path())
        content = file_path.read_text()

        # Should be parseable as ISO format
        parsed = datetime.fromisoformat(content)
        assert parsed is not None

    @pytest.mark.asyncio
    async def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Should create parent directories if they don't exist."""
        logging_overrides = {
            **MINIMAL_CONFIG_DATA["logging"],
            "last_incremental_run_file": "run.log",
        }
        nested_config = create_test_app_config(
            logs_base_dir=str(tmp_path / "deep" / "nested" / "logs"),
            logging=logging_overrides,
        )
        tracker = IncrementalRunTracker(nested_config)

        await tracker.update_last_run_timestamp()

        file_path = Path(tracker.get_last_run_file_path())
        assert file_path.exists()

    @pytest.mark.asyncio
    async def test_handles_write_error_gracefully(self, tracker: IncrementalRunTracker, caplog: pytest.LogCaptureFixture) -> None:
        """Should log warning on write error without raising."""
        with patch("core.run_tracking.Path.open", side_effect=OSError("Permission denied")):
            with caplog.at_level(logging.WARNING):
                # Should not raise
                await tracker.update_last_run_timestamp()

            assert "Failed to update last run timestamp" in caplog.text


class TestGetLastRunTimestamp:
    """Tests for get_last_run_timestamp method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_not_exists(self, tracker: IncrementalRunTracker) -> None:
        """Should return None when no previous run file exists."""
        result = await tracker.get_last_run_timestamp()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_timestamp_from_file(self, tracker: IncrementalRunTracker) -> None:
        """Should return timestamp from existing file."""
        # First update
        await tracker.update_last_run_timestamp()

        # Then read
        result = await tracker.get_last_run_timestamp()

        assert result is not None
        assert isinstance(result, datetime)
        # Should be recent (within last minute)
        assert datetime.now(UTC) - result < timedelta(minutes=1)

    @pytest.mark.asyncio
    async def test_handles_naive_datetime_in_file(self, tracker: IncrementalRunTracker) -> None:
        """Should handle naive datetime by assuming UTC."""
        # Write a naive datetime (no timezone)
        file_path = Path(tracker.get_last_run_file_path())
        file_path.parent.mkdir(parents=True, exist_ok=True)
        naive_timestamp = "2024-01-15T10:30:00"
        file_path.write_text(naive_timestamp)

        result = await tracker.get_last_run_timestamp()

        assert result is not None
        assert result.tzinfo == UTC

    @pytest.mark.asyncio
    async def test_handles_invalid_timestamp_gracefully(self, tracker: IncrementalRunTracker, caplog: pytest.LogCaptureFixture) -> None:
        """Should return None and log warning for invalid timestamp."""
        file_path = Path(tracker.get_last_run_file_path())
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("not a valid timestamp")

        with caplog.at_level(logging.WARNING):
            result = await tracker.get_last_run_timestamp()

        assert result is None
        assert "Failed to read last run timestamp" in caplog.text

    @pytest.mark.asyncio
    async def test_handles_read_error_gracefully(self, tracker: IncrementalRunTracker, caplog: pytest.LogCaptureFixture) -> None:
        """Should return None and log warning on read error."""
        # Create the file first
        file_path = Path(tracker.get_last_run_file_path())
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(datetime.now(UTC).isoformat())

        with patch("core.run_tracking.Path.open", side_effect=OSError("Read error")):
            with caplog.at_level(logging.WARNING):
                result = await tracker.get_last_run_timestamp()

            assert result is None
            assert "Failed to read last run timestamp" in caplog.text


class TestRoundTrip:
    """Integration tests for update/read cycle."""

    @pytest.mark.asyncio
    async def test_write_then_read_preserves_timestamp(self, tracker: IncrementalRunTracker) -> None:
        """Written timestamp should be readable."""
        before = datetime.now(UTC)
        await tracker.update_last_run_timestamp()
        after = datetime.now(UTC)

        result = await tracker.get_last_run_timestamp()

        assert result is not None
        assert before <= result <= after

    @pytest.mark.asyncio
    async def test_multiple_updates_overwrite(self, tracker: IncrementalRunTracker) -> None:
        """Multiple updates should overwrite previous timestamp."""
        await tracker.update_last_run_timestamp()
        first_read = await tracker.get_last_run_timestamp()

        # Small delay to ensure different timestamp
        await asyncio.sleep(0.01)

        await tracker.update_last_run_timestamp()
        second_read = await tracker.get_last_run_timestamp()

        assert first_read is not None
        assert second_read is not None
        assert second_read >= first_read
