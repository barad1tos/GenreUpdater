"""Tests for should_force_scan logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.services.cache.snapshot import LibraryCacheMetadata, LibrarySnapshotService


@pytest.fixture
def snapshot_service() -> LibrarySnapshotService:
    """Create snapshot service with mocked config."""
    config = MagicMock()
    config.library_snapshot = MagicMock()
    config.library_snapshot.enabled = True
    config.library_snapshot.delta_enabled = True
    config.library_snapshot.compress = False
    config.library_snapshot.max_age_hours = 24
    config.library_snapshot.snapshot_dir = "/tmp/test_snapshots"
    config.music_library_path = "/tmp/test_library"
    logger = MagicMock()
    return LibrarySnapshotService(config, logger)


class TestShouldForceScan:
    """Tests for should_force_scan method."""

    @pytest.mark.asyncio
    async def test_force_flag_true_returns_true(self, snapshot_service: LibrarySnapshotService) -> None:
        """CLI --force flag should always trigger force scan."""
        result = await snapshot_service.should_force_scan(force_flag=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_no_metadata_returns_true(self, snapshot_service: LibrarySnapshotService) -> None:
        """First run (no metadata) should trigger force scan."""
        with patch.object(snapshot_service, "get_snapshot_metadata", return_value=None):
            result = await snapshot_service.should_force_scan(force_flag=False)
            assert result is True

    @pytest.mark.asyncio
    async def test_no_last_force_scan_time_returns_true(self, snapshot_service: LibrarySnapshotService) -> None:
        """Missing last_force_scan_time should trigger force scan."""
        metadata = LibraryCacheMetadata(
            version="1.0",
            last_full_scan=datetime.now(UTC),
            library_mtime=datetime.now(UTC),
            track_count=100,
            snapshot_hash="abc123",
            last_force_scan_time=None,
        )
        with patch.object(snapshot_service, "get_snapshot_metadata", return_value=metadata):
            result = await snapshot_service.should_force_scan(force_flag=False)
            assert result is True

    @pytest.mark.asyncio
    async def test_same_day_returns_false(self, snapshot_service: LibrarySnapshotService) -> None:
        """Same calendar day should NOT trigger force scan."""
        now = datetime.now(UTC)
        same_day = now - timedelta(hours=2)
        metadata = LibraryCacheMetadata(
            version="1.0",
            last_full_scan=datetime.now(UTC),
            library_mtime=datetime.now(UTC),
            track_count=100,
            snapshot_hash="abc123",
            last_force_scan_time=same_day.isoformat(),
        )
        with patch.object(snapshot_service, "get_snapshot_metadata", return_value=metadata):
            result = await snapshot_service.should_force_scan(force_flag=False)
            assert result is False

    @pytest.mark.asyncio
    async def test_previous_day_returns_true(self, snapshot_service: LibrarySnapshotService) -> None:
        """Previous calendar day should trigger force scan."""
        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)
        metadata = LibraryCacheMetadata(
            version="1.0",
            last_full_scan=datetime.now(UTC),
            library_mtime=datetime.now(UTC),
            track_count=100,
            snapshot_hash="abc123",
            last_force_scan_time=yesterday.isoformat(),
        )
        with patch.object(snapshot_service, "get_snapshot_metadata", return_value=metadata):
            result = await snapshot_service.should_force_scan(force_flag=False)
            assert result is True


class TestUpdateForceScanTime:
    """Tests for _update_force_scan_time method."""

    @pytest.mark.asyncio
    async def test_updates_metadata_with_current_time(self, snapshot_service: LibrarySnapshotService) -> None:
        """Should update metadata with current timestamp."""
        existing_metadata = LibraryCacheMetadata(
            version="1.0",
            last_full_scan=datetime.now(UTC),
            library_mtime=datetime.now(UTC),
            track_count=100,
            snapshot_hash="abc",
            last_force_scan_time=None,
        )
        with (
            patch.object(snapshot_service, "get_snapshot_metadata", return_value=existing_metadata),
            patch.object(snapshot_service, "update_snapshot_metadata") as mock_update,
        ):
            await snapshot_service._update_force_scan_time()

            mock_update.assert_called_once()
            updated_metadata = mock_update.call_args[0][0]
            assert updated_metadata.last_force_scan_time is not None
            # Verify it's a valid ISO timestamp
            parsed = datetime.fromisoformat(updated_metadata.last_force_scan_time)
            assert parsed.date() == datetime.now(UTC).date()
