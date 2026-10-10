"""Tests for should_force_scan logic.

Logic: Weekly auto-force (7+ days), no first-run force.
- CLI --force: always force
- First run (no metadata): fast mode (nothing to compare)
- < 7 days since last force: fast mode
- 7+ days since last force: auto-force for manual edit detection
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.cache_types import SNAPSHOT_VERSION, LibraryCacheMetadata
from services.cache.snapshot import LibrarySnapshotService
from tests.factories import create_test_app_config


@pytest.fixture
def snapshot_service(tmp_path: Path) -> LibrarySnapshotService:
    """Create snapshot service with test config."""
    config = create_test_app_config(
        logs_base_dir=str(tmp_path),
        music_library_path=str(tmp_path / "Music Library.musiclibrary"),
    )
    logger = MagicMock()
    return LibrarySnapshotService(config, logger)


class TestShouldForceScan:
    """Tests for should_force_scan method."""

    @pytest.mark.asyncio
    async def test_force_flag_true_returns_true(self, snapshot_service: LibrarySnapshotService) -> None:
        """CLI --force flag should always trigger force scan."""
        should_force, reason = await snapshot_service.should_force_scan(force_flag=True)
        assert should_force is True
        assert "CLI --force" in reason

    @pytest.mark.asyncio
    async def test_no_metadata_returns_false(self, snapshot_service: LibrarySnapshotService) -> None:
        """Without a snapshot there is nothing to scan against; the caller reads the whole library anyway."""
        mock_get = AsyncMock(return_value=None)
        with patch.object(snapshot_service, "get_snapshot_metadata", new=mock_get):
            should_force, reason = await snapshot_service.should_force_scan(force_flag=False)
            assert (should_force, reason) == (False, "no snapshot yet")
            mock_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_last_force_scan_time_is_due(self, snapshot_service: LibrarySnapshotService) -> None:
        """A snapshot that never had a full scan gets one, instead of staying in fast mode for good."""
        metadata = LibraryCacheMetadata(
            version=SNAPSHOT_VERSION,
            last_full_scan=datetime.now(UTC),
            library_mtime=datetime.now(UTC),
            track_count=100,
            snapshot_hash="abc123",
            last_force_scan_time=None,
        )
        mock_get = AsyncMock(return_value=metadata)
        with patch.object(snapshot_service, "get_snapshot_metadata", new=mock_get):
            should_force, reason = await snapshot_service.should_force_scan(force_flag=False)
            assert (should_force, reason) == (True, "no full scan recorded")
            mock_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_within_week_returns_false(self, snapshot_service: LibrarySnapshotService) -> None:
        """Force scan within 7 days should NOT trigger force scan."""
        now = datetime.now(UTC)
        three_days_ago = now - timedelta(days=3)
        metadata = LibraryCacheMetadata(
            version=SNAPSHOT_VERSION,
            last_full_scan=datetime.now(UTC),
            library_mtime=datetime.now(UTC),
            track_count=100,
            snapshot_hash="abc123",
            last_force_scan_time=three_days_ago.isoformat(),
        )
        mock_get = AsyncMock(return_value=metadata)
        with patch.object(snapshot_service, "get_snapshot_metadata", new=mock_get):
            should_force, reason = await snapshot_service.should_force_scan(force_flag=False)
            assert should_force is False
            assert "fast mode" in reason
            mock_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_week_old_returns_true(self, snapshot_service: LibrarySnapshotService) -> None:
        """Force scan 7+ days ago should trigger weekly auto-force."""
        now = datetime.now(UTC)
        eight_days_ago = now - timedelta(days=8)
        metadata = LibraryCacheMetadata(
            version=SNAPSHOT_VERSION,
            last_full_scan=datetime.now(UTC),
            library_mtime=datetime.now(UTC),
            track_count=100,
            snapshot_hash="abc123",
            last_force_scan_time=eight_days_ago.isoformat(),
        )
        mock_get = AsyncMock(return_value=metadata)
        with patch.object(snapshot_service, "get_snapshot_metadata", new=mock_get):
            should_force, reason = await snapshot_service.should_force_scan(force_flag=False)
            assert should_force is True
            assert "weekly" in reason
            mock_get.assert_awaited_once()
