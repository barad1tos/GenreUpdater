"""Tests for PipelineSnapshotManager."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline_snapshot import PipelineSnapshotManager
from core.models.track_models import TrackDict


@pytest.fixture
def mock_track_processor() -> MagicMock:
    """Create mock track processor."""
    processor = MagicMock()
    processor.cache_manager = MagicMock()
    processor.cache_manager.update_snapshot = AsyncMock()
    processor.fetch_tracks_by_ids = AsyncMock(return_value=[])
    return processor


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create console logger."""
    return logging.getLogger("test.console")


@pytest.fixture
def manager(mock_track_processor: MagicMock, console_logger: logging.Logger) -> PipelineSnapshotManager:
    """Create PipelineSnapshotManager instance."""
    return PipelineSnapshotManager(
        track_processor=mock_track_processor,
        console_logger=console_logger,
    )


@pytest.fixture
def sample_tracks() -> list[TrackDict]:
    """Create sample tracks."""
    return [
        TrackDict(id="1", name="Track 1", artist="Artist", album="Album", genre="Rock", year="2020"),
        TrackDict(id="2", name="Track 2", artist="Artist", album="Album", genre="Rock", year="2020"),
    ]


class TestPipelineSnapshotManagerInit:
    """Tests for PipelineSnapshotManager initialization."""

    def test_stores_track_processor(self, manager: PipelineSnapshotManager, mock_track_processor: MagicMock) -> None:
        """Should store track processor."""
        assert manager._track_processor is mock_track_processor

    def test_stores_console_logger(self, manager: PipelineSnapshotManager, console_logger: logging.Logger) -> None:
        """Should store console logger."""
        assert manager._console_logger is console_logger

    def test_initializes_snapshot_as_none(self, manager: PipelineSnapshotManager) -> None:
        """Should initialize snapshot as None."""
        assert manager._tracks_snapshot is None

    def test_initializes_index_as_empty(self, manager: PipelineSnapshotManager) -> None:
        """Should initialize index as empty dict."""
        assert manager._tracks_index == {}

    def test_initializes_library_mtime_as_none(self, manager: PipelineSnapshotManager) -> None:
        """Should initialize library_mtime as None."""
        assert manager._captured_library_mtime is None


class TestReset:
    """Tests for reset method."""

    def test_clears_snapshot(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should clear snapshot."""
        manager.set_snapshot(sample_tracks)
        manager.reset()
        assert manager._tracks_snapshot is None

    def test_clears_index(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should clear index."""
        manager.set_snapshot(sample_tracks)
        manager.reset()
        assert manager._tracks_index == {}

    def test_clears_library_mtime(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should clear captured library_mtime."""
        mtime = datetime.now(UTC)
        manager.set_snapshot(sample_tracks, library_mtime=mtime)
        manager.reset()
        assert manager._captured_library_mtime is None


class TestSetSnapshot:
    """Tests for set_snapshot method."""

    def test_stores_tracks(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should store tracks."""
        manager.set_snapshot(sample_tracks)
        assert manager._tracks_snapshot == sample_tracks

    def test_builds_index(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should build track index."""
        manager.set_snapshot(sample_tracks)
        assert "1" in manager._tracks_index
        assert "2" in manager._tracks_index

    def test_clears_previous_index(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should clear previous index before building new one."""
        manager._tracks_index = {"old": MagicMock()}
        manager.set_snapshot(sample_tracks)
        assert "old" not in manager._tracks_index

    def test_stores_library_mtime(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should store library_mtime when provided."""
        mtime = datetime.now(UTC)
        manager.set_snapshot(sample_tracks, library_mtime=mtime)
        assert manager._captured_library_mtime == mtime

    def test_library_mtime_defaults_to_none(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should default library_mtime to None when not provided."""
        manager.set_snapshot(sample_tracks)
        assert manager._captured_library_mtime is None


class TestUpdateTracks:
    """Tests for update_tracks method."""

    def test_returns_early_when_no_index(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should return early when index is empty."""
        manager.update_tracks(sample_tracks)
        # No assertion needed - just verify no error

    def test_skips_tracks_without_id(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should skip tracks without id."""
        manager.set_snapshot(sample_tracks)
        track_no_id = TrackDict(id="", name="No ID", artist="Artist", album="Album", genre="Rock", year="2020")
        manager.update_tracks([track_no_id])
        # No error should occur

    def test_skips_tracks_not_in_index(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should skip tracks not in index."""
        manager.set_snapshot(sample_tracks)
        unknown_track = TrackDict(id="999", name="Unknown", artist="Artist", album="Album", genre="Rock", year="2020")
        manager.update_tracks([unknown_track])
        assert "999" not in manager._tracks_index

    def test_updates_track_fields(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should update track fields."""
        manager.set_snapshot(sample_tracks)
        updated = TrackDict(id="1", name="Updated Name", artist="Artist", album="Album", genre="Pop", year="2021")
        manager.update_tracks([updated])
        assert manager._tracks_index["1"].genre == "Pop"
        assert manager._tracks_index["1"].year == "2021"

    def test_handles_setattr_failure(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should fallback to __dict__ when setattr fails."""
        manager.set_snapshot(sample_tracks)
        track = manager._tracks_index["1"]
        updated = TrackDict(id="1", name="Track 1", artist="Artist", album="Album", genre="NewGenre", year="2020")

        with patch.object(type(track), "__setattr__", side_effect=AttributeError("Cannot set")):
            manager.update_tracks([updated])

        assert track.__dict__.get("genre") == "NewGenre"


class TestGetSnapshot:
    """Tests for get_snapshot method."""

    def test_returns_none_initially(self, manager: PipelineSnapshotManager) -> None:
        """Should return None when no snapshot set."""
        assert manager.get_snapshot() is None

    def test_returns_snapshot(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should return stored snapshot."""
        manager.set_snapshot(sample_tracks)
        assert manager.get_snapshot() == sample_tracks


class TestClear:
    """Tests for clear method."""

    def test_clears_snapshot(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should clear snapshot."""
        manager.set_snapshot(sample_tracks)
        manager.clear()
        assert manager._tracks_snapshot is None

    def test_clears_index(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should clear index."""
        manager.set_snapshot(sample_tracks)
        manager.clear()
        assert manager._tracks_index == {}

    def test_clears_library_mtime(self, manager: PipelineSnapshotManager, sample_tracks: list[TrackDict]) -> None:
        """Should clear captured library_mtime."""
        mtime = datetime.now(UTC)
        manager.set_snapshot(sample_tracks, library_mtime=mtime)
        manager.clear()
        assert manager._captured_library_mtime is None


class TestPersistToDisk:
    """Tests for persist_to_disk method."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_snapshot(self, manager: PipelineSnapshotManager) -> None:
        """Should return False when no snapshot to persist."""
        result = await manager.persist_to_disk()
        assert result is False

    @pytest.mark.asyncio
    async def test_calls_update_snapshot(
        self,
        manager: PipelineSnapshotManager,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should call cache_manager.update_snapshot."""
        manager.set_snapshot(sample_tracks)
        await manager.persist_to_disk()
        mock_track_processor.cache_manager.update_snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_true_on_success(
        self,
        manager: PipelineSnapshotManager,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should return True on successful persist."""
        manager.set_snapshot(sample_tracks)
        result = await manager.persist_to_disk()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(
        self,
        manager: PipelineSnapshotManager,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should return False on exception."""
        manager.set_snapshot(sample_tracks)
        mock_track_processor.cache_manager.update_snapshot = AsyncMock(side_effect=RuntimeError("Disk error"))
        result = await manager.persist_to_disk()
        assert result is False

    @pytest.mark.asyncio
    async def test_passes_library_mtime_override(
        self,
        manager: PipelineSnapshotManager,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should pass captured library_mtime as library_mtime_override."""
        mtime = datetime.now(UTC)
        manager.set_snapshot(sample_tracks, library_mtime=mtime)
        await manager.persist_to_disk()

        mock_track_processor.cache_manager.update_snapshot.assert_called_once()
        call_kwargs = mock_track_processor.cache_manager.update_snapshot.call_args.kwargs
        assert call_kwargs.get("library_mtime_override") == mtime

    @pytest.mark.asyncio
    async def test_passes_none_when_no_library_mtime(
        self,
        manager: PipelineSnapshotManager,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should pass None as library_mtime_override when not captured."""
        manager.set_snapshot(sample_tracks)  # No library_mtime
        await manager.persist_to_disk()

        call_kwargs = mock_track_processor.cache_manager.update_snapshot.call_args.kwargs
        assert call_kwargs.get("library_mtime_override") is None


class TestMergeSmartDelta:
    """Tests for merge_smart_delta method."""

    @pytest.fixture
    def mock_delta(self) -> MagicMock:
        """Create mock delta."""
        delta = MagicMock()
        delta.removed_ids = []
        delta.updated_ids = []
        delta.new_ids = []
        return delta

    @pytest.mark.asyncio
    async def test_removes_deleted_tracks(
        self,
        manager: PipelineSnapshotManager,
        sample_tracks: list[TrackDict],
        mock_delta: MagicMock,
    ) -> None:
        """Should remove deleted tracks."""
        mock_delta.removed_ids = ["1"]
        result = await manager.merge_smart_delta(sample_tracks, mock_delta)
        assert result is not None
        assert len(result) == 1
        assert result[0].id == "2"

    @pytest.mark.asyncio
    async def test_fetches_updated_tracks(
        self,
        manager: PipelineSnapshotManager,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
        mock_delta: MagicMock,
    ) -> None:
        """Should fetch updated tracks."""
        updated_track = TrackDict(id="1", name="Updated", artist="Artist", album="Album", genre="Pop", year="2021")
        mock_delta.updated_ids = ["1"]
        mock_track_processor.fetch_tracks_by_ids = AsyncMock(return_value=[updated_track])

        result = await manager.merge_smart_delta(sample_tracks, mock_delta)

        assert result is not None
        track_1 = next(t for t in result if t.id == "1")
        assert track_1.name == "Updated"

    @pytest.mark.asyncio
    async def test_adds_new_tracks(
        self,
        manager: PipelineSnapshotManager,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
        mock_delta: MagicMock,
    ) -> None:
        """Should add new tracks."""
        new_track = TrackDict(id="3", name="New Track", artist="Artist", album="Album", genre="Jazz", year="2022")
        mock_delta.new_ids = ["3"]
        mock_track_processor.fetch_tracks_by_ids = AsyncMock(return_value=[new_track])

        result = await manager.merge_smart_delta(sample_tracks, mock_delta)

        assert result is not None
        assert len(result) == 3
        assert any(t.id == "3" for t in result)

    @pytest.mark.asyncio
    async def test_returns_none_when_tracks_missing(
        self,
        manager: PipelineSnapshotManager,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
        mock_delta: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should return None when fetched tracks are missing."""
        mock_delta.updated_ids = ["1"]
        mock_track_processor.fetch_tracks_by_ids = AsyncMock(return_value=[])

        with caplog.at_level(logging.WARNING):
            result = await manager.merge_smart_delta(sample_tracks, mock_delta)

        assert result is None
        assert "falling back to batch scan" in caplog.text

    @pytest.mark.asyncio
    async def test_handles_empty_delta(
        self,
        manager: PipelineSnapshotManager,
        sample_tracks: list[TrackDict],
        mock_delta: MagicMock,
    ) -> None:
        """Should handle empty delta."""
        result = await manager.merge_smart_delta(sample_tracks, mock_delta)
        assert result is not None
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_logs_merge_stats(
        self,
        manager: PipelineSnapshotManager,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
        mock_delta: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log merge statistics."""
        new_track = TrackDict(id="3", name="New", artist="Artist", album="Album", genre="Jazz", year="2022")
        mock_delta.new_ids = ["3"]
        mock_delta.updated_ids = []
        mock_delta.removed_ids = ["1"]
        mock_track_processor.fetch_tracks_by_ids = AsyncMock(return_value=[new_track])

        with caplog.at_level(logging.INFO):
            await manager.merge_smart_delta(sample_tracks, mock_delta)

        assert "Smart Delta merged" in caplog.text
