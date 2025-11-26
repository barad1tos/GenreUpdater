"""CSV Utility Functions.

Shared CSV operations used by reports and track sync modules.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.logger import ensure_directory

if TYPE_CHECKING:
    import logging
    from collections.abc import Sequence


def save_csv(
    data: Sequence[dict[str, str]],
    fieldnames: Sequence[str],
    file_path: str,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    data_type: str,
) -> None:
    """Save the provided data to a CSV file.

    Checks if the target directory for the CSV file exists, and creates it if not.
    Uses atomic write pattern with a temporary file.

    Args:
        data: List of dictionaries to save to the CSV file.
        fieldnames: List of field names for the CSV file.
        file_path: Path to the CSV file.
        console_logger: Logger for console output.
        error_logger: Logger for error output.
        data_type: Type of data being saved (e.g., "tracks", "changes report").

    """
    ensure_directory(str(Path(file_path).parent))
    console_logger.info("Saving %s to CSV: %s", data_type, file_path)

    temp_file_path = f"{file_path}.tmp"

    try:
        with Path(temp_file_path).open(mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            # Filter each row to include only keys present in fieldnames
            for row in data:
                filtered_row = {field: row.get(field, "") for field in fieldnames}
                writer.writerow(filtered_row)

        # Atomic rename
        # On Windows, os.replace provides atomic replacement if the destination exists
        # On POSIX, os.rename is atomic
        Path(temp_file_path).replace(Path(file_path))

        console_logger.info(
            "%s saved to %s (%d entries).",
            data_type.capitalize(),
            file_path,
            len(data),
        )
    except (OSError, UnicodeError):
        error_logger.exception("Failed to save %s", data_type)
        # Clean up temporary file in case of error
        if Path(temp_file_path).exists():
            try:
                Path(temp_file_path).unlink()
            except OSError as cleanup_e:
                error_logger.warning(
                    "Failed to remove temporary file %s: %s",
                    temp_file_path,
                    cleanup_e,
                )
