"""iCloud conflict file cleanup utilities.

This module provides utilities to detect and clean up iCloud sync conflict files.
When iCloud detects conflicting edits, it creates files like:
- original.json → original 2.json, original 3.json, etc.

These utilities safely clean up such conflicts by keeping the most recent version.

For repository-wide cleanup, use `scan_for_all_conflicts` and `cleanup_conflict_files`.
"""

from __future__ import annotations

import argparse
import fnmatch
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Pattern to match iCloud conflict files/folders: "name N" or "name N.ext"
# Examples: ".coverage 2", "data 2", "file 3.txt", "script 2.sh"
ICLOUD_CONFLICT_PATTERN = re.compile(r"^(.+) (\d+)(\.[^.]+)?$")

# Minimum age in seconds before we consider a file safe to delete
# Files younger than this might still be syncing
DEFAULT_MIN_AGE_SECONDS = 600

# Module-level logger for internal functions
_logger = logging.getLogger(__name__)


@dataclass
class CleanupContext:
    """Context for cleanup operations."""

    base_file: Path
    winner_path: Path
    winner_mtime: float
    now: float
    min_age_seconds: int
    dry_run: bool
    files_renamed: int = 0


def find_icloud_conflicts(base_file: Path) -> list[Path]:
    """Find iCloud conflict files for a given base file.

    Args:
        base_file: The original file path (e.g., "cache/generic_cache.json")

    Returns:
        List of conflict file paths sorted by modification time (oldest first)

    Example:
        >>> find_icloud_conflicts(Path("cache/generic_cache.json"))
        [Path("cache/generic_cache 2.json"), Path("cache/generic_cache 3.json")]

    """
    if not base_file.parent.exists():
        return []

    stem = base_file.stem
    suffix = base_file.suffix
    parent = base_file.parent

    conflicts: list[Path] = []

    for file_path in parent.iterdir():
        if not file_path.is_file():
            continue

        if match := ICLOUD_CONFLICT_PATTERN.match(file_path.name):
            matched_stem = match.group(1)
            matched_suffix = match.group(3)

            if matched_stem == stem and matched_suffix == suffix:
                conflicts.append(file_path)

    # Sort by modification time (oldest first)
    return sorted(conflicts, key=lambda p: p.stat().st_mtime)


def _is_file_too_recent(mtime: float, now: float, min_age_seconds: int) -> bool:
    """Check if a file is too recent to safely modify."""
    return (now - mtime) < min_age_seconds


def _handle_winner_rename(ctx: CleanupContext, logger: logging.Logger) -> int:
    """Handle renaming winner conflict file to base file name.

    Returns:
        Number of files renamed (0 or 1)
    """
    if ctx.winner_path == ctx.base_file:
        return 0

    age_seconds = ctx.now - ctx.winner_mtime

    if _is_file_too_recent(ctx.winner_mtime, ctx.now, ctx.min_age_seconds):
        logger.warning(
            "iCloud cleanup: Winner '%s' is too recent (%.1fs old), skipping rename to avoid sync race",
            ctx.winner_path.name,
            age_seconds,
        )
        return 0

    if ctx.dry_run:
        logger.info("iCloud cleanup [DRY RUN]: Would rename '%s' → '%s'", ctx.winner_path.name, ctx.base_file.name)
        return 1  # Simulate successful rename for accurate dry run reporting

    # Rename winner to base file (atomic on same filesystem)
    # If base file exists, rename() will replace it atomically
    try:
        ctx.winner_path.rename(ctx.base_file)
        logger.info("iCloud cleanup: Renamed '%s' → '%s'", ctx.winner_path.name, ctx.base_file.name)
        return 1
    except OSError:
        logger.exception("iCloud cleanup: Failed to rename '%s' → '%s'", ctx.winner_path.name, ctx.base_file.name)
        return 0


def _should_skip_conflict(conflict: Path, ctx: CleanupContext) -> bool:
    """Check if a conflict file should be skipped during deletion."""
    if conflict != ctx.winner_path:
        return False

    # Winner that wasn't renamed (still at conflict path)
    if ctx.winner_path != ctx.base_file and ctx.files_renamed == 0:
        return True

    # Winner was renamed, file no longer exists at this path
    return ctx.files_renamed > 0


def _delete_conflict(conflict: Path, ctx: CleanupContext, logger: logging.Logger) -> int:
    """Delete a single conflict file.

    Returns:
        Number of files deleted (0 or 1)
    """
    conflict_mtime = conflict.stat().st_mtime

    if _is_file_too_recent(conflict_mtime, ctx.now, ctx.min_age_seconds):
        age_seconds = ctx.now - conflict_mtime
        logger.warning(
            "iCloud cleanup: Conflict '%s' is too recent (%.1fs old), skipping deletion",
            conflict.name,
            age_seconds,
        )
        return 0

    if ctx.dry_run:
        logger.info("iCloud cleanup [DRY RUN]: Would delete '%s'", conflict.name)
        return 0

    conflict.unlink()
    logger.info("iCloud cleanup: Deleted conflict file '%s'", conflict.name)
    return 1


def cleanup_icloud_conflicts(
    base_file: Path,
    logger: logging.Logger,
    *,
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Clean up iCloud conflict files for a given base file.

    Strategy:
    1. Find all conflict files
    2. Compare modification times of base file and conflicts
    3. Keep the most recently modified file as the "winner"
    4. If winner is a conflict file, rename it to base file name
    5. Delete all other conflict files

    Args:
        base_file: The original file path
        logger: Logger for status messages
        min_age_seconds: Minimum file age before deletion (avoids sync races)
        dry_run: If True, only log what would be done without making changes

    Returns:
        Tuple of (files_deleted, files_renamed)

    """
    conflicts = find_icloud_conflicts(base_file)

    if not conflicts:
        return 0, 0

    # Gather all candidates (base + conflicts) with their modification times
    candidates: list[tuple[Path, float]] = []

    if base_file.exists():
        candidates.append((base_file, base_file.stat().st_mtime))

    candidates.extend((conflict, conflict.stat().st_mtime) for conflict in conflicts)

    if not candidates:
        return 0, 0

    # Find the most recently modified file
    winner_path, winner_mtime = max(candidates, key=lambda x: x[1])

    logger.info(
        "iCloud cleanup: Found %d conflict file(s) for '%s'. Winner: '%s' (mtime: %s)",
        len(conflicts),
        base_file.name,
        winner_path.name,
        datetime.fromtimestamp(winner_mtime, tz=UTC).isoformat(),
    )

    # Create context for cleanup operations
    ctx = CleanupContext(
        base_file=base_file,
        winner_path=winner_path,
        winner_mtime=winner_mtime,
        now=datetime.now(UTC).timestamp(),
        min_age_seconds=min_age_seconds,
        dry_run=dry_run,
    )

    # Handle winner rename if needed
    ctx.files_renamed = _handle_winner_rename(ctx, logger)

    # Delete conflict files
    files_deleted = sum(_delete_conflict(conflict, ctx, logger) for conflict in conflicts if not _should_skip_conflict(conflict, ctx))

    return files_deleted, ctx.files_renamed


def cleanup_cache_directory(
    cache_dir: Path,
    file_patterns: list[str],
    logger: logging.Logger,
    *,
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
) -> dict[str, tuple[int, int]]:
    """Clean up iCloud conflicts for multiple files in a cache directory.

    Args:
        cache_dir: Directory containing cache files
        file_patterns: List of base file names to check (e.g., ["generic_cache.json", "album_years.csv"])
        logger: Logger for status messages
        min_age_seconds: Minimum file age before deletion
        dry_run: If True, only log what would be done

    Returns:
        Dict mapping file names to (deleted_count, renamed_count) tuples

    """
    if not cache_dir.exists():
        logger.debug("iCloud cleanup: Cache directory '%s' does not exist", cache_dir)
        return {}

    results: dict[str, tuple[int, int]] = {}

    for pattern in file_patterns:
        base_file = cache_dir / pattern
        deleted, renamed = cleanup_icloud_conflicts(
            base_file,
            logger,
            min_age_seconds=min_age_seconds,
            dry_run=dry_run,
        )
        if deleted > 0 or renamed > 0:
            results[pattern] = (deleted, renamed)

    total_deleted = sum(d for d, _ in results.values())
    total_renamed = sum(r for _, r in results.values())

    if total_deleted > 0 or total_renamed > 0:
        logger.info(
            "iCloud cleanup complete: %d file(s) deleted, %d file(s) renamed",
            total_deleted,
            total_renamed,
        )

    return results


# =============================================================================
# Repository-wide conflict scanning and cleanup
# =============================================================================


@dataclass
class ConflictInfo:
    """Information about a detected iCloud conflict file or folder."""

    conflict_path: Path
    base_name: str
    conflict_number: int
    extension: str  # Empty string if no extension
    mtime: float
    size: int
    is_directory: bool = False

    @property
    def base_file_name(self) -> str:
        """Return the expected base file/folder name without conflict number."""
        return f"{self.base_name}{self.extension}"


@dataclass
class ScanResult:
    """Result of scanning a directory for iCloud conflicts."""

    conflicts: list[ConflictInfo] = field(default_factory=list)
    scanned_files: int = 0
    scanned_dirs: int = 0

    def add_conflict(self, conflict: ConflictInfo) -> None:
        """Add a conflict to the results."""
        self.conflicts.append(conflict)

    @property
    def total_conflicts(self) -> int:
        """Return total number of conflicts found."""
        return len(self.conflicts)

    def group_by_base_name(self) -> dict[Path, list[ConflictInfo]]:
        """Group conflicts by their base file path."""
        groups: dict[Path, list[ConflictInfo]] = {}
        for conflict in self.conflicts:
            base_path = conflict.conflict_path.parent / conflict.base_file_name
            if base_path not in groups:
                groups[base_path] = []
            groups[base_path].append(conflict)
        return groups


def is_icloud_conflict(path: Path) -> ConflictInfo | None:
    """Check if a path is an iCloud conflict and return info if so.

    Handles both files and directories.

    Args:
        path: Path to check (file or directory)

    Returns:
        ConflictInfo if the path is an iCloud conflict, None otherwise

    """
    if not path.exists():
        return None

    match = ICLOUD_CONFLICT_PATTERN.match(path.name)
    if not match:
        return None

    try:
        stat = path.stat()
        is_dir = path.is_dir()
        return ConflictInfo(
            conflict_path=path,
            base_name=match.group(1),
            conflict_number=int(match.group(2)),
            extension=match.group(3) or "",  # None → empty string
            mtime=stat.st_mtime,
            size=0 if is_dir else stat.st_size,
            is_directory=is_dir,
        )
    except OSError:
        return None


DEFAULT_EXCLUDE_DIRS: set[str] = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".tox",
    "dist",
    "build",
    ".eggs",
    "*.egg-info",
}


def _should_exclude_dir(name: str, exclude_dirs: set[str]) -> bool:
    """Check if directory name should be excluded (supports glob patterns)."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in exclude_dirs)


def _should_exclude_path(path: Path, exclude_patterns: list[str] | None) -> bool:
    """Check if path should be excluded based on patterns."""
    if not exclude_patterns:
        return False
    return any(path.match(pattern) for pattern in exclude_patterns)


def _process_entry(
    entry: Path,
    result: ScanResult,
    exclude_patterns: list[str] | None,
) -> None:
    """Process a single file or directory entry during scanning."""
    if _should_exclude_path(entry, exclude_patterns):
        return
    if conflict_info := is_icloud_conflict(entry):
        result.add_conflict(conflict_info)


def _scan_directory_recursive(
    directory: Path,
    result: ScanResult,
    exclude_dirs: set[str],
    exclude_patterns: list[str] | None,
) -> None:
    """Recursively scan a directory for conflicts."""
    try:
        entries = list(directory.iterdir())
    except PermissionError:
        _logger.warning("Permission denied when scanning directory: %s", directory)
        return

    result.scanned_dirs += 1

    for entry in entries:
        if entry.is_dir():
            # Check if directory itself is a conflict (e.g., "data 2")
            _process_entry(entry, result, exclude_patterns)
            # Recurse into directory if not excluded
            if not _should_exclude_dir(entry.name, exclude_dirs):
                _scan_directory_recursive(entry, result, exclude_dirs, exclude_patterns)
        else:
            result.scanned_files += 1
            _process_entry(entry, result, exclude_patterns)


def scan_for_all_conflicts(
    root_dir: Path,
    *,
    exclude_dirs: set[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> ScanResult:
    """Scan a directory recursively for all iCloud conflict files.

    Args:
        root_dir: Root directory to scan
        exclude_dirs: Directory names to skip (default: common non-source dirs)
        exclude_patterns: Glob patterns to exclude from results

    Returns:
        ScanResult with all found conflicts

    """
    result = ScanResult()
    if not root_dir.exists():
        return result

    dirs_to_exclude = exclude_dirs if exclude_dirs is not None else DEFAULT_EXCLUDE_DIRS
    _scan_directory_recursive(root_dir, result, dirs_to_exclude, exclude_patterns)
    return result


def cleanup_conflict_files(
    conflicts: list[ConflictInfo],
    logger: logging.Logger,
    *,
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Delete iCloud conflict files and folders (for repository cleanup).

    Unlike cleanup_icloud_conflicts which picks a winner, this function
    simply deletes all conflict files/folders since the git-tracked version is
    the authoritative source for repository files.

    Args:
        conflicts: List of conflict files/folders to delete
        logger: Logger for status messages
        min_age_seconds: Minimum file age before deletion
        dry_run: If True, only log what would be done

    Returns:
        Tuple of (deleted_count, skipped_count)

    """

    now = datetime.now(UTC).timestamp()
    deleted = 0
    skipped = 0

    for conflict in conflicts:
        age_seconds = now - conflict.mtime
        item_type = "folder" if conflict.is_directory else "file"

        if age_seconds < min_age_seconds:
            logger.warning(
                "iCloud cleanup: Skipping %s '%s' (%.1fs old, min age: %ds)",
                item_type,
                conflict.conflict_path,
                age_seconds,
                min_age_seconds,
            )
            skipped += 1
            continue

        if dry_run:
            logger.info(
                "iCloud cleanup [DRY RUN]: Would delete %s '%s' (conflict #%d for '%s')",
                item_type,
                conflict.conflict_path,
                conflict.conflict_number,
                conflict.base_file_name,
            )
            deleted += 1  # Count as deleted for dry run reporting
        else:
            try:
                if conflict.is_directory:
                    shutil.rmtree(conflict.conflict_path)
                else:
                    conflict.conflict_path.unlink()
                logger.info(
                    "iCloud cleanup: Deleted %s '%s' (conflict #%d for '%s')",
                    item_type,
                    conflict.conflict_path,
                    conflict.conflict_number,
                    conflict.base_file_name,
                )
                deleted += 1
            except OSError as e:
                logger.exception("iCloud cleanup: Failed to delete %s '%s': %s", item_type, conflict.conflict_path, e)
                skipped += 1

    return deleted, skipped


def cleanup_repository(
    root_dir: Path,
    logger: logging.Logger,
    *,
    exclude_dirs: set[str] | None = None,
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
) -> dict[str, int]:
    """Scan and clean up all iCloud conflicts in a repository.

    This is the main entry point for repository-wide cleanup.

    Args:
        root_dir: Root directory of the repository
        logger: Logger for status messages
        exclude_dirs: Directory names to skip
        min_age_seconds: Minimum file age before deletion
        dry_run: If True, only log what would be done

    Returns:
        Dict with statistics: scanned_files, scanned_dirs, conflicts_found, deleted, skipped

    """
    logger.info("iCloud cleanup: Scanning '%s' for conflict files...", root_dir)

    scan_result = scan_for_all_conflicts(root_dir, exclude_dirs=exclude_dirs)

    logger.info(
        "iCloud cleanup: Scanned %d files in %d directories, found %d conflict(s)",
        scan_result.scanned_files,
        scan_result.scanned_dirs,
        scan_result.total_conflicts,
    )

    if scan_result.total_conflicts == 0:
        return {
            "scanned_files": scan_result.scanned_files,
            "scanned_dirs": scan_result.scanned_dirs,
            "conflicts_found": 0,
            "deleted": 0,
            "skipped": 0,
        }

    # Log grouped conflicts
    grouped = scan_result.group_by_base_name()
    for base_path, file_conflicts in grouped.items():
        logger.info(
            "  %s: %d conflict(s) → %s",
            base_path.parent.name,
            len(file_conflicts),
            ", ".join(c.conflict_path.name for c in file_conflicts),
        )

    deleted, skipped = cleanup_conflict_files(
        scan_result.conflicts,
        logger,
        min_age_seconds=min_age_seconds,
        dry_run=dry_run,
    )

    if not dry_run:
        logger.info(
            "iCloud cleanup complete: %d deleted, %d skipped",
            deleted,
            skipped,
        )

    return {
        "scanned_files": scan_result.scanned_files,
        "scanned_dirs": scan_result.scanned_dirs,
        "conflicts_found": scan_result.total_conflicts,
        "deleted": deleted,
        "skipped": skipped,
    }


# =============================================================================
# CLI Entrypoint - Run as: uv run python -m src.core.utils.icloud_cleanup [path]
# =============================================================================


def main() -> None:
    """CLI entrypoint for iCloud conflict cleanup."""
    parser = argparse.ArgumentParser(
        description="Scan and clean up iCloud conflict files and folders",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (preview what would be deleted)
  uv run python -m src.core.utils.icloud_cleanup . --dry-run

  # Actually delete conflicts
  uv run python -m src.core.utils.icloud_cleanup .

  # Scan specific directory
  uv run python -m src.core.utils.icloud_cleanup /path/to/project --dry-run
""",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be deleted, don't actually delete",
    )
    parser.add_argument(
        "--min-age",
        type=int,
        default=DEFAULT_MIN_AGE_SECONDS,
        help=f"Minimum file age in seconds before deletion (default: {DEFAULT_MIN_AGE_SECONDS})",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only show summary, not individual files",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
    )
    logger = logging.getLogger("icloud_cleanup")

    root_path = Path(args.path).resolve()
    if not root_path.exists():
        logger.error("Path does not exist: %s", root_path)
        raise SystemExit(1)

    result = cleanup_repository(
        root_path,
        logger,
        min_age_seconds=args.min_age,
        dry_run=args.dry_run,
    )

    # Summary
    if args.dry_run:
        print(f"\n[DRY RUN] Would delete {result['deleted']} conflict(s)")
    else:
        print(f"\nDeleted {result['deleted']} conflict(s), skipped {result['skipped']}")


if __name__ == "__main__":
    main()
