"""Tests for TrackCacheManager - track caching and snapshot operations."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models.track_models import TrackDict
from src.core.tracks.cache_manager import TrackCacheManager
from src.services.cache.snapshot import LibraryCacheMetadata, LibraryDeltaCache


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test.cache_manager")


@pytest.fixture
def mock_cache_service() -> AsyncMock:
    """Create a mock cache service."""
    service = AsyncMock()
    service.get_async = AsyncMock(return_value=None)
    service.set_async = AsyncMock()
    return service


@pytest.fixture
def mock_snapshot_service() -> AsyncMock:
    """Create a mock snapshot service."""
    service = AsyncMock()
    service.load_snapshot = AsyncMock(return_value=None)
    service.save_snapshot = AsyncMock(return_value="snapshot_hash_123")
    service.is_snapshot_valid = AsyncMock(return_value=False)
    service.is_enabled = MagicMock(return_value=True)
    service.is_delta_enabled = MagicMock(return_value=True)
    service.get_snapshot_metadata = AsyncMock(return_value=None)
    service.update_snapshot_metadata = AsyncMock()
    service.load_delta = AsyncMock(return_value=None)
    service.save_delta = AsyncMock()
    service.get_library_mtime = AsyncMock(return_value=datetime.now(UTC))
    return service


@pytest.fixture
def cache_manager(
    mock_cache_service: AsyncMock,
    mock_snapshot_service: AsyncMock,
    logger: logging.Logger,
) -> TrackCacheManager:
    """Create a TrackCacheManager instance."""
    return TrackCacheManager(
        cache_service=mock_cache_service,
        snapshot_service=mock_snapshot_service,
        console_logger=logger,
    )


@pytest.fixture
def sample_tracks() -> list[TrackDict]:
    """Create sample track data."""
    return [
        TrackDict(id="1", name="Song A", artist="Artist 1", album="Album 1", genre="Rock", year="2020"),
        TrackDict(id="2", name="Song B", artist="Artist 1", album="Album 1", genre="Rock", year="2020"),
        TrackDict(id="3", name="Song C", artist="Artist 2", album="Album 2", genre="Pop", year="2021"),
    ]


class TestGetCachedTracks:
    """Tests for get_cached_tracks method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_cache_empty(
        self, cache_manager: TrackCacheManager
    ) -> None:
        """Test returns None when cache is empty."""
        result = await cache_manager.get_cached_tracks("test_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_tracks_from_cache(
        self,
        cache_manager: TrackCacheManager,
        mock_cache_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test returns tracks from cache."""
        mock_cache_service.get_async.return_value = sample_tracks

        result = await cache_manager.get_cached_tracks("test_key")

        assert result is not None
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_validates_track_data(
        self,
        cache_manager: TrackCacheManager,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test validates track data structure."""
        # Invalid track - missing required fields
        invalid_tracks = [{"invalid": "data"}]
        mock_cache_service.get_async.return_value = invalid_tracks

        result = await cache_manager.get_cached_tracks("test_key")

        assert result is None


class TestLoadSnapshot:
    """Tests for load_snapshot method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_snapshot_service(
        self,
        mock_cache_service: AsyncMock,
        logger: logging.Logger,
    ) -> None:
        """Test returns None when snapshot service is not available."""
        manager = TrackCacheManager(
            cache_service=mock_cache_service,
            snapshot_service=None,
            console_logger=logger,
        )

        result = await manager.load_snapshot()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_snapshot_missing(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
    ) -> None:
        """Test returns None when snapshot is missing."""
        mock_snapshot_service.load_snapshot.return_value = None

        result = await cache_manager.load_snapshot()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_tracks_when_snapshot_valid(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test returns tracks when snapshot is valid."""
        mock_snapshot_service.load_snapshot.return_value = sample_tracks
        mock_snapshot_service.is_snapshot_valid.return_value = True

        result = await cache_manager.load_snapshot()

        assert result == sample_tracks

    @pytest.mark.asyncio
    async def test_returns_none_when_snapshot_stale(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test returns None when snapshot is stale."""
        mock_snapshot_service.load_snapshot.return_value = sample_tracks
        mock_snapshot_service.is_snapshot_valid.return_value = False

        result = await cache_manager.load_snapshot()

        assert result is None


class TestGetSnapshotForDeltaUpdate:
    """Tests for get_snapshot_for_delta_update method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_snapshot_service(
        self,
        mock_cache_service: AsyncMock,
        logger: logging.Logger,
    ) -> None:
        """Test returns (None, None) when no snapshot service."""
        manager = TrackCacheManager(
            cache_service=mock_cache_service,
            snapshot_service=None,
            console_logger=logger,
        )

        tracks, min_date = await manager.get_snapshot_for_delta_update()

        assert tracks is None
        assert min_date is None

    @pytest.mark.asyncio
    async def test_returns_tracks_only_when_snapshot_valid(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test returns (tracks, None) when snapshot is valid."""
        mock_snapshot_service.load_snapshot.return_value = sample_tracks
        mock_snapshot_service.is_snapshot_valid.return_value = True

        tracks, min_date = await cache_manager.get_snapshot_for_delta_update()

        assert tracks == sample_tracks
        assert min_date is None

    @pytest.mark.asyncio
    async def test_returns_none_when_delta_disabled(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test returns (None, None) when delta updates disabled."""
        mock_snapshot_service.load_snapshot.return_value = sample_tracks
        mock_snapshot_service.is_snapshot_valid.return_value = False
        mock_snapshot_service.is_delta_enabled.return_value = False

        tracks, min_date = await cache_manager.get_snapshot_for_delta_update()

        assert tracks is None
        assert min_date is None

    @pytest.mark.asyncio
    async def test_returns_tracks_with_min_date_for_delta(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test returns tracks and min_date for delta update."""
        last_scan = datetime.now(UTC) - timedelta(hours=1)
        metadata = LibraryCacheMetadata(
            last_full_scan=last_scan,
            library_mtime=datetime.now(UTC),
            track_count=3,
            snapshot_hash="hash123",
        )

        mock_snapshot_service.load_snapshot.return_value = sample_tracks
        mock_snapshot_service.is_snapshot_valid.return_value = False
        mock_snapshot_service.get_snapshot_metadata.return_value = metadata
        mock_snapshot_service.load_delta.return_value = None

        tracks, min_date = await cache_manager.get_snapshot_for_delta_update()

        assert tracks == sample_tracks
        assert min_date == last_scan


class TestMergeTracks:
    """Tests for merge_tracks static method."""

    def test_merge_no_updates(self, sample_tracks: list[TrackDict]) -> None:
        """Test merge with no updates."""
        result = TrackCacheManager.merge_tracks(sample_tracks, [])

        assert len(result) == 3
        assert result == sample_tracks

    def test_merge_with_updates(self, sample_tracks: list[TrackDict]) -> None:
        """Test merge with track updates."""
        updated_track = TrackDict(
            id="1", name="Updated Song A", artist="Artist 1",
            album="Album 1", genre="Rock", year="2021"
        )

        result = TrackCacheManager.merge_tracks(sample_tracks, [updated_track])

        assert len(result) == 3
        assert result[0].name == "Updated Song A"
        assert result[0].year == "2021"

    def test_merge_with_new_tracks(self, sample_tracks: list[TrackDict]) -> None:
        """Test merge adds new tracks."""
        new_track = TrackDict(
            id="4", name="New Song", artist="Artist 3",
            album="Album 3", genre="Jazz", year="2022"
        )

        result = TrackCacheManager.merge_tracks(sample_tracks, [new_track])

        assert len(result) == 4
        assert result[-1].id == "4"

    def test_merge_preserves_order(self, sample_tracks: list[TrackDict]) -> None:
        """Test merge preserves track order."""
        updated_track = TrackDict(
            id="2", name="Updated Song B", artist="Artist 1",
            album="Album 1", genre="Rock", year="2020"
        )

        result = TrackCacheManager.merge_tracks(sample_tracks, [updated_track])

        # Order should be preserved: 1, 2, 3
        assert result[0].id == "1"
        assert result[1].id == "2"
        assert result[2].id == "3"

    def test_merge_mixed_updates_and_new(self, sample_tracks: list[TrackDict]) -> None:
        """Test merge with both updates and new tracks."""
        updates = [
            TrackDict(id="1", name="Updated A", artist="Artist 1", album="Album 1", genre="Rock", year="2020"),
            TrackDict(id="4", name="New Song", artist="Artist 4", album="Album 4", genre="Pop", year="2022"),
        ]

        result = TrackCacheManager.merge_tracks(sample_tracks, updates)

        assert len(result) == 4
        assert result[0].name == "Updated A"
        assert result[-1].id == "4"


class TestUpdateSnapshot:
    """Tests for update_snapshot method."""

    @pytest.mark.asyncio
    async def test_does_nothing_when_no_snapshot_service(
        self,
        mock_cache_service: AsyncMock,
        logger: logging.Logger,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test does nothing when no snapshot service."""
        manager = TrackCacheManager(
            cache_service=mock_cache_service,
            snapshot_service=None,
            console_logger=logger,
        )

        await manager.update_snapshot(sample_tracks)
        # No exception should be raised

    @pytest.mark.asyncio
    async def test_does_nothing_when_disabled(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test does nothing when snapshot service disabled."""
        mock_snapshot_service.is_enabled.return_value = False

        await cache_manager.update_snapshot(sample_tracks)

        mock_snapshot_service.save_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_saves_snapshot_and_metadata(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test saves snapshot and updates metadata."""
        await cache_manager.update_snapshot(sample_tracks)

        mock_snapshot_service.save_snapshot.assert_called_once_with(sample_tracks)
        mock_snapshot_service.update_snapshot_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_delta_cache(
        self,
        mock_cache_service: AsyncMock,
        logger: logging.Logger,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test updates delta cache."""
        # Use naive datetime to match _now() in snapshot.py
        fixed_time_naive = datetime(2025, 1, 1, 12, 0, 0)
        fixed_time_aware = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

        # Create fresh mock - get_library_mtime returns aware, but delta uses naive internally
        mock_snapshot = AsyncMock()
        mock_snapshot.is_enabled = MagicMock(return_value=True)
        mock_snapshot.is_delta_enabled = MagicMock(return_value=True)
        mock_snapshot.save_snapshot = AsyncMock(return_value="hash")
        mock_snapshot.get_library_mtime = AsyncMock(return_value=fixed_time_aware)
        mock_snapshot.update_snapshot_metadata = AsyncMock()
        mock_snapshot.load_delta = AsyncMock(return_value=None)
        mock_snapshot.save_delta = AsyncMock()

        manager = TrackCacheManager(
            cache_service=mock_cache_service,
            snapshot_service=mock_snapshot,
            console_logger=logger,
            current_time_func=lambda: fixed_time_naive,
        )

        await manager.update_snapshot(sample_tracks, processed_track_ids=["1", "2"])

        mock_snapshot.save_delta.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_delta_when_disabled(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test skips delta update when disabled."""
        mock_snapshot_service.is_delta_enabled.return_value = False

        await cache_manager.update_snapshot(sample_tracks)

        mock_snapshot_service.save_delta.assert_not_called()


class TestCanUseSnapshot:
    """Tests for can_use_snapshot method."""

    def test_returns_false_when_no_service(
        self,
        mock_cache_service: AsyncMock,
        logger: logging.Logger,
    ) -> None:
        """Test returns False when no snapshot service."""
        manager = TrackCacheManager(
            cache_service=mock_cache_service,
            snapshot_service=None,
            console_logger=logger,
        )

        assert manager.can_use_snapshot() is False

    def test_returns_false_when_disabled(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
    ) -> None:
        """Test returns False when snapshot disabled."""
        mock_snapshot_service.is_enabled.return_value = False

        assert cache_manager.can_use_snapshot() is False

    def test_returns_true_when_enabled(
        self,
        cache_manager: TrackCacheManager,
        mock_snapshot_service: AsyncMock,
    ) -> None:
        """Test returns True when snapshot enabled."""
        mock_snapshot_service.is_enabled.return_value = True

        assert cache_manager.can_use_snapshot() is True


class TestCustomTimeFunc:
    """Tests for custom time function."""

    @pytest.mark.asyncio
    async def test_uses_custom_time_func(
        self,
        mock_cache_service: AsyncMock,
        mock_snapshot_service: AsyncMock,
        logger: logging.Logger,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Test uses custom time function for timestamps."""
        fixed_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

        manager = TrackCacheManager(
            cache_service=mock_cache_service,
            snapshot_service=mock_snapshot_service,
            console_logger=logger,
            current_time_func=lambda: fixed_time,
        )

        await manager.update_snapshot(sample_tracks)

        # Check that the metadata was created with the fixed time
        call_args = mock_snapshot_service.update_snapshot_metadata.call_args
        metadata = call_args[0][0]
        assert metadata.last_full_scan == fixed_time
