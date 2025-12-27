#!/usr/bin/env python3
"""Migrate CSV headers from old_year/new_year to year_before_mgu/year_set_by_mgu.

This script:
1. Creates timestamped backups of all CSV files
2. Renames headers in-place
3. Preserves all data

Usage:
    uv run python scripts/migrate_csv_headers.py [--dry-run] [--logs-base PATH]

Options:
    --dry-run         Show what would be changed without modifying files
    --logs-base PATH  Override the logs directory (default: $MGU_LOGS_BASE or iCloud path)

Environment:
    MGU_LOGS_BASE     Base directory for logs (overrides default path)
"""

from __future__ import annotations

import csv
import os
import shutil
import sys
from datetime import datetime, UTC
from pathlib import Path

# Header mappings: old_name -> new_name
HEADER_MIGRATIONS = {
    "old_year": "year_before_mgu",
    "new_year": "year_set_by_mgu",
}

# Default logs path (can be overridden via --logs-base or MGU_LOGS_BASE env var)
_DEFAULT_LOGS_BASE = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs"


def _get_logs_base() -> Path:
    """Get logs base directory from CLI args, env var, or default."""
    # Check CLI args for --logs-base
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--logs-base" and i < len(sys.argv) - 1:
            return Path(sys.argv[i + 1])
        if arg.startswith("--logs-base="):
            return Path(arg.split("=", 1)[1])

    # Check environment variable
    env_path = os.environ.get("MGU_LOGS_BASE")
    return Path(env_path) if env_path else _DEFAULT_LOGS_BASE


def _get_csv_files(logs_base: Path) -> list[Path]:
    """Get list of CSV files to migrate."""
    return [
        logs_base / "csv/track_list.csv",
        logs_base / "csv/changes_report.csv",  # May not exist yet
    ]


def backup_file(file_path: Path) -> Path:
    """Create a timestamped backup of the file."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = file_path.with_suffix(f".backup_{timestamp}.csv")
    _ = shutil.copy2(file_path, backup_path)
    return backup_path


def migrate_headers(file_path: Path, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Migrate headers in a CSV file.

    Returns:
        (changed, messages) tuple
    """
    messages: list[str] = []

    if not file_path.exists():
        messages.append(f"  [SKIP] File does not exist: {file_path}")
        return False, messages

    # Read the file
    with file_path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        messages.append(f"  [SKIP] Empty file: {file_path}")
        return False, messages

    # Check headers
    original_headers = rows[0]
    new_headers: list[str] = []
    changed = False

    for header in original_headers:
        if header in HEADER_MIGRATIONS:
            new_headers.append(HEADER_MIGRATIONS[header])
            messages.append(f"  [RENAME] '{header}' → '{HEADER_MIGRATIONS[header]}'")
            changed = True
        else:
            new_headers.append(header)

    if not changed:
        messages.append(f"  [OK] Headers already migrated: {file_path}")
        return False, messages

    if dry_run:
        messages.append(f"  [DRY-RUN] Would update: {file_path}")
        return True, messages

    # Backup before modifying
    backup_path = backup_file(file_path)
    messages.append(f"  [BACKUP] Created: {backup_path}")

    # Write with new headers
    rows[0] = new_headers
    with file_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    messages.append(f"  [DONE] Updated: {file_path}")
    return True, messages


def main() -> int:
    """Run the migration."""
    dry_run = "--dry-run" in sys.argv
    logs_base = _get_logs_base()
    csv_files = _get_csv_files(logs_base)

    print("=" * 60)
    print("CSV Header Migration: old_year/new_year → year_before_mgu/year_set_by_mgu")
    print("=" * 60)
    print(f"Logs base: {logs_base}")

    if dry_run:
        print("\n⚠️  DRY RUN MODE - No files will be modified\n")
    else:
        print("\n🔧 LIVE MODE - Files will be backed up and modified\n")

    total_changed = 0

    for csv_path in csv_files:
        print(f"\nProcessing: {csv_path}")

        changed, messages = migrate_headers(csv_path, dry_run=dry_run)
        for msg in messages:
            print(msg)

        if changed:
            total_changed += 1

    print("\n" + "=" * 60)
    if dry_run:
        print(f"Summary: {total_changed} file(s) would be modified")
        print("Run without --dry-run to apply changes")
    else:
        print(f"Summary: {total_changed} file(s) migrated")
        print("Backups created with .backup_YYYYMMDD_HHMMSS.csv suffix")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
