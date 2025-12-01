"""Unit tests for iCloud conflict cleanup utilities."""


from __future__ import annotations

import logging
import os
import time
from pathlib import Path
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
    cleanup_conflict_files,
    cleanup_icloud_conflicts,
    cleanup_repository,
    find_icloud_conflicts,
    is_icloud_conflict,
)


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def mock_logger() -> MagicMock:
    """Create a mock logger."""
    return MagicMock(spec=logging.Logger)


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

    def test_matches_file_without_extension(self) -> None:
        """Test pattern matches files without extension (e.g., .coverage 2)."""
        match = ICLOUD_CONFLICT_PATTERN.match(".coverage 2")
        assert match is not None
        assert match.group(1) == ".coverage"
        assert match.group(2) == "2"
        assert match.group(3) is None  # No extension

    def test_matches_folder_without_extension(self) -> None:
        """Test pattern matches folder names (e.g., data 2)."""
        match = ICLOUD_CONFLICT_PATTERN.match("data 2")
        assert match is not None
        assert match.group(1) == "data"
        assert match.group(2) == "2"
        assert match.group(3) is None  # No extension

    def test_matches_folder_multiple_words(self) -> None:
        """Test pattern matches multi-word folder names."""
        match = ICLOUD_CONFLICT_PATTERN.match("allure-results 11")
        assert match is not None
        assert match.group(1) == "allure-results"
        assert match.group(2) == "11"
        assert match.group(3) is None


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
        """Test dry run logs but doesn't rename, returns 1 to simulate success."""
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
        assert result == 1  # Returns 1 to simulate successful rename
        assert winner.exists()  # Still exists (no actual rename)
        assert not base.exists()  # Not created (no actual rename)

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

        deleted, renamed = cleanup_icloud_conflicts(base_file, mock_logger, min_age_seconds=60)
        assert deleted == 2
        assert renamed == 0
        assert base_file.exists()
        assert not conflict2.exists()
        assert not conflict3.exists()

    def test_conflict_is_winner_renames_and_deletes(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test renames winner conflict and deletes others."""

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

        deleted, renamed = cleanup_icloud_conflicts(base_file, mock_logger, min_age_seconds=60)
        assert renamed == 1
        assert deleted == 1  # conflict3 deleted
        assert base_file.exists()
        assert base_file.read_text() == '{"winner": true}'
        assert not conflict2.exists()
        assert not conflict3.exists()

    def test_dry_run_no_changes(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test dry run makes no changes."""

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

    def test_respects_min_age_preserves_new_conflict(
        self, temp_cache_dir: Path, mock_logger: MagicMock
    ) -> None:
        """Test that conflicts newer than min_age_seconds are preserved."""
        base_file = temp_cache_dir / "data.json"
        base_file.write_text("{}")

        # Create a conflict file that is newer than the threshold
        conflict = temp_cache_dir / "data 2.json"
        conflict.write_text('{"conflict": true}')

        now = time.time()
        os.utime(conflict, (now, now))

        # New file (younger than min_age_seconds) should be skipped
        deleted, renamed = cleanup_icloud_conflicts(
            base_file,
            mock_logger,
            min_age_seconds=60,
        )
        assert deleted == 0
        assert renamed == 0
        assert conflict.exists()

    def test_respects_min_age_deletes_old_conflict(
        self, temp_cache_dir: Path, mock_logger: MagicMock
    ) -> None:
        """Test that conflicts older than min_age_seconds are deleted."""
        base_file = temp_cache_dir / "data.json"
        base_file.write_text("{}")

        # Create a conflict file
        conflict = temp_cache_dir / "data 2.json"
        conflict.write_text('{"conflict": true}')

        # Age the conflict file beyond the threshold
        now = time.time()
        old_time = now - 120  # 2 minutes ago
        os.utime(conflict, (old_time, old_time))

        # Old conflict should now be cleaned up
        deleted, renamed = cleanup_icloud_conflicts(
            base_file,
            mock_logger,
            min_age_seconds=60,
        )
        assert deleted == 1
        assert renamed == 0
        assert not conflict.exists()


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
            min_age_seconds=60,
        )

        assert "cache.json" in results
        assert results["cache.json"] == (1, 0)  # 1 deleted, 0 renamed
        assert "other.json" not in results

    def test_logs_summary(self, temp_cache_dir: Path, mock_logger: MagicMock) -> None:
        """Test logs summary when files cleaned."""

        old_time = time.time() - 120
        (temp_cache_dir / "cache.json").write_text("{}")
        conflict = temp_cache_dir / "cache 2.json"
        conflict.write_text("{}")
        os.utime(conflict, (old_time, old_time))

        cleanup_cache_directory(temp_cache_dir, ["cache.json"], mock_logger, min_age_seconds=60)

        # Check summary was logged
        mock_logger.info.assert_called()
        calls = [str(c) for c in mock_logger.info.call_args_list]
        assert any("complete" in c for c in calls)


class TestIsICloudConflict:
    """Tests for is_icloud_conflict function."""

    def test_returns_none_for_nonexistent_path(self, tmp_path: Path) -> None:
        """Test returns None for nonexistent paths."""
        result = is_icloud_conflict(tmp_path / "nonexistent 2.txt")
        assert result is None

    def test_returns_none_for_non_conflict(self, tmp_path: Path) -> None:
        """Test returns None for normal files."""
        normal_file = tmp_path / "test.txt"
        normal_file.write_text("content")
        assert is_icloud_conflict(normal_file) is None

    def test_detects_conflict_file_with_extension(self, tmp_path: Path) -> None:
        """Test detects conflict file with extension."""
        conflict = tmp_path / "test 2.txt"
        conflict.write_text("content")

        result = is_icloud_conflict(conflict)
        assert result is not None
        assert result.base_name == "test"
        assert result.conflict_number == 2
        assert result.extension == ".txt"
        assert result.is_directory is False

    def test_detects_conflict_file_without_extension(self, tmp_path: Path) -> None:
        """Test detects extensionless conflict files (e.g., .coverage 2)."""
        conflict = tmp_path / ".coverage 3"
        conflict.write_text("coverage data")

        result = is_icloud_conflict(conflict)
        assert result is not None
        assert result.base_name == ".coverage"
        assert result.conflict_number == 3
        assert result.extension == ""
        assert result.is_directory is False

    def test_detects_conflict_directory(self, tmp_path: Path) -> None:
        """Test detects conflict directories (e.g., data 2)."""
        conflict_dir = tmp_path / "data 2"
        conflict_dir.mkdir()

        result = is_icloud_conflict(conflict_dir)
        assert result is not None
        assert result.base_name == "data"
        assert result.conflict_number == 2
        assert result.extension == ""
        assert result.is_directory is True
        assert result.size == 0  # Directories have size 0

    def test_detects_folder_with_hyphen(self, tmp_path: Path) -> None:
        """Test detects folders with hyphens in name."""
        conflict_dir = tmp_path / "allure-results 5"
        conflict_dir.mkdir()

        result = is_icloud_conflict(conflict_dir)
        assert result is not None
        assert result.base_name == "allure-results"
        assert result.conflict_number == 5
        assert result.is_directory is True


class TestCleanupConflictFilesWithDirectories:
    """Tests for cleanup_conflict_files with directory support."""

    def test_deletes_conflict_directory(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test deletes conflict directories using shutil.rmtree."""

        # Create conflict directory with content
        conflict_dir = tmp_path / "data 2"
        conflict_dir.mkdir()
        (conflict_dir / "file.txt").write_text("content")
        (conflict_dir / "subdir").mkdir()
        (conflict_dir / "subdir" / "nested.txt").write_text("nested")

        # Make it old enough
        old_time = time.time() - 120
        os.utime(conflict_dir, (old_time, old_time))

        conflict_info = is_icloud_conflict(conflict_dir)
        assert conflict_info is not None

        deleted, skipped = cleanup_conflict_files([conflict_info], mock_logger, min_age_seconds=60)

        assert deleted == 1
        assert skipped == 0
        assert not conflict_dir.exists()

    def test_dry_run_preserves_directory(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test dry run doesn't delete directories but reports what would be deleted."""

        conflict_dir = tmp_path / "monitoring 2"
        conflict_dir.mkdir()

        old_time = time.time() - 120
        os.utime(conflict_dir, (old_time, old_time))

        conflict_info = is_icloud_conflict(conflict_dir)
        assert conflict_info is not None

        deleted, skipped = cleanup_conflict_files(
            [conflict_info], mock_logger, min_age_seconds=60, dry_run=True
        )

        assert deleted == 1  # Reports what WOULD be deleted
        assert skipped == 0
        assert conflict_dir.exists()  # But not actually deleted

    def test_mixed_files_and_directories(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test cleans up both files and directories."""

        old_time = time.time() - 120

        # Create conflict file
        conflict_file = tmp_path / "cache 2.json"
        conflict_file.write_text("{}")
        os.utime(conflict_file, (old_time, old_time))

        # Create conflict directory
        conflict_dir = tmp_path / "data 2"
        conflict_dir.mkdir()
        os.utime(conflict_dir, (old_time, old_time))

        conflict_infos = [
            is_icloud_conflict(conflict_file),
            is_icloud_conflict(conflict_dir),
        ]
        conflicts = [c for c in conflict_infos if c is not None]

        deleted, _ = cleanup_conflict_files(conflicts, mock_logger, min_age_seconds=60)

        assert deleted == 2
        assert not conflict_file.exists()
        assert not conflict_dir.exists()


class TestCleanupRepository:
    """Tests for cleanup_repository function."""

    def test_empty_directory_returns_zeros(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test returns zeros for empty directory."""
        result = cleanup_repository(tmp_path, mock_logger)
        assert result["conflicts_found"] == 0
        assert result["deleted"] == 0
        assert result["skipped"] == 0

    def test_scans_files_and_dirs(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test counts scanned files and directories."""
        # Create some structure
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("# main")
        (subdir / "utils.py").write_text("# utils")

        result = cleanup_repository(tmp_path, mock_logger)
        assert result["scanned_files"] >= 2
        assert result["scanned_dirs"] >= 1

    def test_finds_and_cleans_conflicts(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test finds and cleans conflict files."""

        old_time = time.time() - 120

        # Create conflict file
        conflict = tmp_path / "test 2.txt"
        conflict.write_text("conflict")
        os.utime(conflict, (old_time, old_time))

        result = cleanup_repository(tmp_path, mock_logger, min_age_seconds=60)
        assert result["conflicts_found"] == 1
        assert result["deleted"] == 1
        assert not conflict.exists()

    def test_finds_extensionless_conflicts(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test finds extensionless conflict files like .coverage 2."""

        old_time = time.time() - 120

        conflict = tmp_path / ".coverage 2"
        conflict.write_text("coverage")
        os.utime(conflict, (old_time, old_time))

        result = cleanup_repository(tmp_path, mock_logger, min_age_seconds=60)
        assert result["conflicts_found"] == 1
        assert result["deleted"] == 1

    def test_finds_directory_conflicts(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test finds and cleans conflict directories."""

        old_time = time.time() - 120

        # Create conflict directory
        conflict_dir = tmp_path / "data 2"
        conflict_dir.mkdir()
        (conflict_dir / "file.txt").write_text("content")
        os.utime(conflict_dir, (old_time, old_time))

        result = cleanup_repository(tmp_path, mock_logger, min_age_seconds=60)
        assert result["conflicts_found"] == 1
        assert result["deleted"] == 1
        assert not conflict_dir.exists()

    def test_dry_run_preserves_all(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test dry run reports what would be deleted but preserves files."""

        old_time = time.time() - 120

        conflict_file = tmp_path / "test 2.txt"
        conflict_file.write_text("conflict")
        os.utime(conflict_file, (old_time, old_time))

        conflict_dir = tmp_path / "data 2"
        conflict_dir.mkdir()
        os.utime(conflict_dir, (old_time, old_time))

        result = cleanup_repository(tmp_path, mock_logger, min_age_seconds=60, dry_run=True)
        assert result["conflicts_found"] == 2
        assert result["deleted"] == 2  # Reports what WOULD be deleted
        assert conflict_file.exists()  # But not actually deleted
        assert conflict_dir.exists()  # But not actually deleted

    def test_excludes_git_directory(self, tmp_path: Path, mock_logger: MagicMock) -> None:
        """Test excludes .git directory from scanning."""
        old_time = time.time() - 120

        # Create .git dir with conflict inside (should be ignored)
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        conflict_in_git = git_dir / "config 2"
        conflict_in_git.write_text("ignored")
        os.utime(conflict_in_git, (old_time, old_time))

        # Create conflict outside .git (should be found)
        conflict = tmp_path / "test 2.txt"
        conflict.write_text("found")
        os.utime(conflict, (old_time, old_time))

        result = cleanup_repository(tmp_path, mock_logger)
        assert result["conflicts_found"] == 1
        assert conflict_in_git.exists()  # Still exists - was excluded
