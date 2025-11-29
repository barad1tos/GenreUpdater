"""Unit tests for iCloud conflict cleanup utilities."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from src.core.utils.icloud_cleanup import (
    ICLOUD_CONFLICT_PATTERN,
    CleanupContext,
    _delete_conflict,
    _handle_winner_rename,
    _is_file_too_recent,
    _should_skip_conflict,
    cleanup_cache_directory,
    cleanup_icloud_conflicts,
    find_icloud_conflicts,
)

if TYPE_CHECKING:
    pass


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def mock_logger() -> MagicMock:
    """Create a mock logger."""
    logger = MagicMock(spec=logging.Logger)
    return logger


class TestICloudConflictPattern:
    """Tests for the iCloud conflict regex pattern."""

    def test_matches_numbered_conflict(self) -> None:
        """Test pattern matches standard iCloud conflict files."""
        match = ICLOUD_CONFLICT_PATTERN.match("generic_cache 2.json")
        assert match is not None
        assert match.group(1) == "generic_cache"
        assert match.group(2) == "2"
        assert match.group(3) == ".json"

    def test_matches_higher_numbers(self) -> None:
        """Test pattern matches higher conflict numbers."""
        match = ICLOUD_CONFLICT_PATTERN.match("file 123.txt")
        assert match is not None
        assert match.group(2) == "123"

    def test_no_match_original_file(self) -> None:
        """Test pattern doesn't match original files."""
        assert ICLOUD_CONFLICT_PATTERN.match("generic_cache.json") is None

    def test_no_match_no_space(self) -> None:
        """Test pattern requires space before number."""
        assert ICLOUD_CONFLICT_PATTERN.match("file2.txt") is None

    def test_matches_complex_filename(self) -> None:
        """Test pattern works with complex filenames."""
        # Pattern matches "filename N.ext" where N is just a number
        match = ICLOUD_CONFLICT_PATTERN.match("my file name 5.json")
        assert match is not None
        assert match.group(1) == "my file name"
        assert match.group(2) == "5"
        assert match.group(3) == ".json"


class TestIsFileTooRecent:
    """Tests for _is_file_too_recent helper."""

    def test_file_is_recent(self) -> None:
        """Test returns True for recent files."""
        now = 1000.0
        mtime = 990.0  # 10 seconds ago
        assert _is_file_too_recent(mtime, now, min_age_seconds=60) is True

    def test_file_is_old_enough(self) -> None:
        """Test returns False for old enough files."""
        now = 1000.0
        mtime = 900.0  # 100 seconds ago
        assert _is_file_too_recent(mtime, now, min_age_seconds=60) is False

    def test_file_exactly_at_threshold(self) -> None:
        """Test file exactly at threshold is not too recent."""
        now = 1000.0
        mtime = 940.0  # 60 seconds ago
        assert _is_file_too_recent(mtime, now, min_age_seconds=60) is False


class TestFindICloudConflicts:
    """Tests for find_icloud_conflicts function."""

    def test_no_conflicts(self, temp_cache_dir: Path) -> None:
        """Test returns empty list when no conflicts exist."""
        base_file = temp_cache_dir / "cache.json"
        base_file.write_text("{}")

        conflicts = find_icloud_conflicts(base_file)
        assert conflicts == []

    def test_finds_conflicts(self, temp_cache_dir: Path) -> None:
        """Test finds all conflict files."""
        base_file = temp_cache_dir / "cache.json"
        base_file.write_text("{}")

        # Create conflict files
        (temp_cache_dir / "cache 2.json").write_text("{}")
        (temp_cache_dir / "cache 3.json").write_text("{}")

        conflicts = find_icloud_conflicts(base_file)
        assert len(conflicts) == 2
        conflict_names = [c.name for c in conflicts]
        assert "cache 2.json" in conflict_names
        assert "cache 3.json" in conflict_names

    def test_ignores_different_extensions(self, temp_cache_dir: Path) -> None:
        """Test ignores files with different extensions."""
        base_file = temp_cache_dir / "cache.json"
        base_file.write_text("{}")

        (temp_cache_dir / "cache 2.json").write_text("{}")
        (temp_cache_dir / "cache 2.txt").write_text("")  # Different extension

        conflicts = find_icloud_conflicts(base_file)
        assert len(conflicts) == 1
        assert conflicts[0].name == "cache 2.json"

    def test_ignores_different_base_names(self, temp_cache_dir: Path) -> None:
        """Test ignores files with different base names."""
        base_file = temp_cache_dir / "cache.json"
        base_file.write_text("{}")

        (temp_cache_dir / "cache 2.json").write_text("{}")
        (temp_cache_dir / "other 2.json").write_text("{}")  # Different name

        conflicts = find_icloud_conflicts(base_file)
        assert len(conflicts) == 1

    def test_sorted_by_mtime(self, temp_cache_dir: Path) -> None:
        """Test conflicts are sorted by modification time."""
        base_file = temp_cache_dir / "cache.json"
        base_file.write_text("{}")

        # Create files with different mtimes
        conflict2 = temp_cache_dir / "cache 2.json"
        conflict3 = temp_cache_dir / "cache 3.json"

        conflict3.write_text("{}")
        time.sleep(0.01)  # Ensure different mtime
        conflict2.write_text("{}")

        conflicts = find_icloud_conflicts(base_file)
        # conflict3 was created first, so it should be first
        assert conflicts[0].name == "cache 3.json"
        assert conflicts[1].name == "cache 2.json"

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Test returns empty list for nonexistent directory."""
        base_file = tmp_path / "nonexistent" / "cache.json"
        conflicts = find_icloud_conflicts(base_file)
        assert conflicts == []

    def test_ignores_directories(self, temp_cache_dir: Path) -> None:
        """Test ignores directories matching the pattern."""
        base_file = temp_cache_dir / "cache.json"
        base_file.write_text("{}")

        # Create a directory with conflict-like name
        (temp_cache_dir / "cache 2.json").mkdir()

        conflicts = find_icloud_conflicts(base_file)
        assert conflicts == []


class TestCleanupContext:
    """Tests for CleanupContext dataclass."""

    def test_default_files_renamed(self, tmp_path: Path) -> None:
        """Test default files_renamed is 0."""
        ctx = CleanupContext(
            base_file=tmp_path / "test.json",
            winner_path=tmp_path / "test.json",
            winner_mtime=1000.0,
            now=2000.0,
            min_age_seconds=60,
            dry_run=False,
        )
        assert ctx.files_renamed == 0


class TestShouldSkipConflict:
    """Tests for _should_skip_conflict helper."""

    def test_not_winner_not_skipped(self, tmp_path: Path) -> None:
        """Test non-winner conflict is not skipped."""
        base = tmp_path / "test.json"
        winner = tmp_path / "test 2.json"
        conflict = tmp_path / "test 3.json"

        ctx = CleanupContext(
            base_file=base,
            winner_path=winner,
            winner_mtime=1000.0,
            now=2000.0,
            min_age_seconds=60,
            dry_run=False,
            files_renamed=0,
        )

        assert _should_skip_conflict(conflict, ctx) is False

    def test_winner_not_renamed_skipped(self, tmp_path: Path) -> None:
        """Test winner that wasn't renamed is skipped."""
        base = tmp_path / "test.json"
        winner = tmp_path / "test 2.json"

        ctx = CleanupContext(
            base_file=base,
            winner_path=winner,
            winner_mtime=1000.0,
            now=2000.0,
            min_age_seconds=60,
            dry_run=False,
            files_renamed=0,  # Winner wasn't renamed
        )

        assert _should_skip_conflict(winner, ctx) is True

    def test_winner_renamed_skipped(self, tmp_path: Path) -> None:
        """Test winner that was renamed is skipped (no longer exists)."""
        base = tmp_path / "test.json"
        winner = tmp_path / "test 2.json"

        ctx = CleanupContext(
            base_file=base,
            winner_path=winner,
            winner_mtime=1000.0,
            now=2000.0,
            min_age_seconds=60,
            dry_run=False,
            files_renamed=1,  # Winner was renamed
        )

        assert _should_skip_conflict(winner, ctx) is True


class TestHandleWinnerRename:
    """Tests for _handle_winner_rename helper."""

    def test_winner_is_base_no_rename(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test no rename when winner is already the base file."""
        base = tmp_path / "test.json"
        base.write_text("{}")

        ctx = CleanupContext(
            base_file=base,
            winner_path=base,  # Winner is base
            winner_mtime=1000.0,
            now=2000.0,
            min_age_seconds=60,
            dry_run=False,
        )

        result = _handle_winner_rename(ctx, mock_logger)
        assert result == 0

    def test_winner_too_recent_no_rename(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test no rename when winner is too recent."""
        base = tmp_path / "test.json"
        winner = tmp_path / "test 2.json"
        winner.write_text("{}")

        ctx = CleanupContext(
            base_file=base,
            winner_path=winner,
            winner_mtime=1990.0,  # Only 10 seconds ago
            now=2000.0,
            min_age_seconds=60,
            dry_run=False,
        )

        result = _handle_winner_rename(ctx, mock_logger)
        assert result == 0
        mock_logger.warning.assert_called_once()

    def test_dry_run_no_actual_rename(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test dry run logs but doesn't rename."""
        base = tmp_path / "test.json"
        winner = tmp_path / "test 2.json"
        winner.write_text("{}")

        ctx = CleanupContext(
            base_file=base,
            winner_path=winner,
            winner_mtime=1000.0,
            now=2000.0,
            min_age_seconds=60,
            dry_run=True,
        )

        result = _handle_winner_rename(ctx, mock_logger)
        assert result == 0
        assert winner.exists()  # Still exists
        assert not base.exists()  # Not created

    def test_actual_rename(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test actual rename when conditions are met."""
        base = tmp_path / "test.json"
        winner = tmp_path / "test 2.json"
        winner.write_text('{"winner": true}')

        ctx = CleanupContext(
            base_file=base,
            winner_path=winner,
            winner_mtime=1000.0,
            now=2000.0,
            min_age_seconds=60,
            dry_run=False,
        )

        result = _handle_winner_rename(ctx, mock_logger)
        assert result == 1
        assert base.exists()
        assert not winner.exists()
        assert base.read_text() == '{"winner": true}'

    def test_rename_removes_old_base(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test rename removes old base file first."""
        base = tmp_path / "test.json"
        base.write_text('{"old": true}')
        winner = tmp_path / "test 2.json"
        winner.write_text('{"winner": true}')

        ctx = CleanupContext(
            base_file=base,
            winner_path=winner,
            winner_mtime=1000.0,
            now=2000.0,
            min_age_seconds=60,
            dry_run=False,
        )

        result = _handle_winner_rename(ctx, mock_logger)
        assert result == 1
        assert base.read_text() == '{"winner": true}'


class TestDeleteConflict:
    """Tests for _delete_conflict helper."""

    def test_file_too_recent_not_deleted(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test recent file is not deleted."""
        conflict = tmp_path / "test 2.json"
        conflict.write_text("{}")

        now = time.time()
        ctx = CleanupContext(
            base_file=tmp_path / "test.json",
            winner_path=tmp_path / "test.json",
            winner_mtime=now,
            now=now,
            min_age_seconds=60,
            dry_run=False,
        )

        result = _delete_conflict(conflict, ctx, mock_logger)
        assert result == 0
        assert conflict.exists()
        mock_logger.warning.assert_called_once()

    def test_dry_run_not_deleted(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test dry run doesn't delete."""
        conflict = tmp_path / "test 2.json"
        conflict.write_text("{}")

        # Set old mtime
        import os
        old_time = time.time() - 120
        os.utime(conflict, (old_time, old_time))

        ctx = CleanupContext(
            base_file=tmp_path / "test.json",
            winner_path=tmp_path / "test.json",
            winner_mtime=time.time(),
            now=time.time(),
            min_age_seconds=60,
            dry_run=True,
        )

        result = _delete_conflict(conflict, ctx, mock_logger)
        assert result == 0
        assert conflict.exists()

    def test_actual_delete(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test actual deletion."""
        conflict = tmp_path / "test 2.json"
        conflict.write_text("{}")

        # Set old mtime
        import os
        old_time = time.time() - 120
        os.utime(conflict, (old_time, old_time))

        ctx = CleanupContext(
            base_file=tmp_path / "test.json",
            winner_path=tmp_path / "test.json",
            winner_mtime=time.time(),
            now=time.time(),
            min_age_seconds=60,
            dry_run=False,
        )

        result = _delete_conflict(conflict, ctx, mock_logger)
        assert result == 1
        assert not conflict.exists()


class TestCleanupICloudConflicts:
    """Tests for cleanup_icloud_conflicts function."""

    def test_no_conflicts_returns_zero(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test returns (0, 0) when no conflicts."""
        base_file = temp_cache_dir / "cache.json"
        base_file.write_text("{}")

        deleted, renamed = cleanup_icloud_conflicts(base_file, mock_logger)
        assert deleted == 0
        assert renamed == 0

    def test_base_is_winner_deletes_conflicts(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test deletes conflicts when base file is winner."""
        import os

        base_file = temp_cache_dir / "cache.json"
        conflict2 = temp_cache_dir / "cache 2.json"
        conflict3 = temp_cache_dir / "cache 3.json"

        # Create old conflicts
        old_time = time.time() - 120
        conflict2.write_text("{}")
        conflict3.write_text("{}")
        os.utime(conflict2, (old_time, old_time))
        os.utime(conflict3, (old_time, old_time))

        # Create newer base file
        base_file.write_text("{}")

        deleted, renamed = cleanup_icloud_conflicts(base_file, mock_logger)
        assert deleted == 2
        assert renamed == 0
        assert base_file.exists()
        assert not conflict2.exists()
        assert not conflict3.exists()

    def test_conflict_is_winner_renames_and_deletes(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test renames winner conflict and deletes others."""
        import os

        base_file = temp_cache_dir / "cache.json"
        conflict2 = temp_cache_dir / "cache 2.json"
        conflict3 = temp_cache_dir / "cache 3.json"

        old_time = time.time() - 120  # 2 minutes ago
        winner_time = time.time() - 70  # 70 seconds ago (older than min_age but newer than others)

        # Create old base and conflict3
        base_file.write_text('{"old": true}')
        conflict3.write_text('{"old": true}')
        os.utime(base_file, (old_time, old_time))
        os.utime(conflict3, (old_time, old_time))

        # Create conflict2 (winner) - newer than others but old enough to rename
        conflict2.write_text('{"winner": true}')
        os.utime(conflict2, (winner_time, winner_time))

        deleted, renamed = cleanup_icloud_conflicts(base_file, mock_logger)
        assert renamed == 1
        assert deleted == 1  # conflict3 deleted
        assert base_file.exists()
        assert base_file.read_text() == '{"winner": true}'
        assert not conflict2.exists()
        assert not conflict3.exists()

    def test_dry_run_no_changes(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test dry run makes no changes."""
        import os

        base_file = temp_cache_dir / "cache.json"
        conflict2 = temp_cache_dir / "cache 2.json"

        old_time = time.time() - 120
        conflict2.write_text("{}")
        os.utime(conflict2, (old_time, old_time))

        base_file.write_text("{}")

        deleted, renamed = cleanup_icloud_conflicts(base_file, mock_logger, dry_run=True)
        assert deleted == 0
        assert renamed == 0
        assert conflict2.exists()


class TestCleanupCacheDirectory:
    """Tests for cleanup_cache_directory function."""

    def test_nonexistent_directory(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test handles nonexistent directory."""
        cache_dir = tmp_path / "nonexistent"
        results = cleanup_cache_directory(cache_dir, ["cache.json"], mock_logger)
        assert results == {}

    def test_no_conflicts_empty_results(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test returns empty dict when no conflicts."""
        (temp_cache_dir / "cache.json").write_text("{}")
        (temp_cache_dir / "other.json").write_text("{}")

        results = cleanup_cache_directory(
            temp_cache_dir,
            ["cache.json", "other.json"],
            mock_logger,
        )
        assert results == {}

    def test_multiple_patterns_with_conflicts(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test handles multiple file patterns with conflicts."""
        import os

        old_time = time.time() - 120

        # cache.json has conflicts
        (temp_cache_dir / "cache.json").write_text("{}")
        conflict = temp_cache_dir / "cache 2.json"
        conflict.write_text("{}")
        os.utime(conflict, (old_time, old_time))

        # other.json has no conflicts
        (temp_cache_dir / "other.json").write_text("{}")

        results = cleanup_cache_directory(
            temp_cache_dir,
            ["cache.json", "other.json"],
            mock_logger,
        )

        assert "cache.json" in results
        assert results["cache.json"] == (1, 0)  # 1 deleted, 0 renamed
        assert "other.json" not in results

    def test_logs_summary(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test logs summary when files cleaned."""
        import os

        old_time = time.time() - 120
        (temp_cache_dir / "cache.json").write_text("{}")
        conflict = temp_cache_dir / "cache 2.json"
        conflict.write_text("{}")
        os.utime(conflict, (old_time, old_time))

        cleanup_cache_directory(temp_cache_dir, ["cache.json"], mock_logger)

        # Check summary was logged
        mock_logger.info.assert_called()
        calls = [str(c) for c in mock_logger.info.call_args_list]
        assert any("complete" in c for c in calls)
