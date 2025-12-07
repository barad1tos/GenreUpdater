"""Batch processing functionality for Music Genre Updater."""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.music_updater import MusicUpdater


class BatchProcessor:
    """Handles batch processing of multiple artists."""

    def __init__(
        self,
        music_updater: "MusicUpdater",
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Initialize batch processor.

        Args:
            music_updater: Music updater instance
            console_logger: Console logger
            error_logger: Error logger

        """
        self.music_updater = music_updater
        self.console_logger = console_logger
        self.error_logger = error_logger

    # noinspection PyTypeChecker
    async def process_from_file(self, file_path: str, operation: str = "full", force: bool = False) -> dict[str, list[str]]:
        """Process artists from a file.

        Args:
            file_path: Path to a file with artist names
            operation: Operation to perform (clean, years, full)
            force: Force processing

        Returns:
            Dictionary with successful and failed artists

        """
        # Read artist names from the file
        try:
            path = Path(file_path)
            if not path.exists():
                self.error_logger.error("File not found: %s", file_path)
                return {"successful": [], "failed": [], "skipped": []}

            # Use run_in_executor to avoid blocking async operation
            loop = asyncio.get_running_loop()

            def _read_file() -> list[str]:
                with path.open(encoding="utf-8") as f:
                    return [line.strip() for line in f if line.strip()]

            artists = await loop.run_in_executor(None, _read_file)

        except (OSError, ValueError):
            self.error_logger.exception("Error reading file %s", file_path)
            return {"successful": [], "failed": [], "skipped": []}

        self.console_logger.info(
            "Starting batch processing of %d artists from %s",
            len(artists),
            file_path,
        )

        # Process the artists using the dedicated method
        return await self.process_artists(artists, operation, force)

    async def process_artists(self, artists: list[str], operation: str = "full", force: bool = False) -> dict[str, list[str]]:
        """Process a list of artists.

        Args:
            artists: List of artist names to process
            operation: Operation to perform (clean, years, full)
            force: Force processing (used for year updates)

        Returns:
            Dictionary with successful and failed artists

        """
        results: dict[str, list[str]] = {"successful": [], "failed": [], "skipped": []}

        # Process each artist
        for i, artist in enumerate(artists, 1):
            self.console_logger.info("\n[%d/%d] Processing artist: %s", i, len(artists), artist)

            try:
                if operation == "clean":
                    await self.music_updater.run_clean_artist(artist)
                elif operation == "years":
                    await self.music_updater.run_update_years(artist, force)
                else:  # full
                    # First clean, then update genres and years
                    await self.music_updater.run_clean_artist(artist)
                    # Genre update happens as part of the main pipeline
                    await self.music_updater.run_update_years(artist, force)

                results["successful"].append(artist)
                self.console_logger.info("✅ Successfully processed: %s", artist)

            except asyncio.CancelledError:
                # Properly handle cancellation of async tasks
                self.console_logger.warning("\nBatch processing interrupted (async task cancelled)")
                # Include current artist (i-1) since enumerate starts at 1
                results["skipped"].extend(artists[i - 1 :])
                raise  # Re-raise to propagate cancellation

            except (OSError, ValueError, TypeError, RuntimeError):
                results["failed"].append(artist)
                self.error_logger.exception("❌ Failed to process %s", artist)

        # Summary
        self.print_summary(results, len(artists))
        return results

    def print_summary(self, results: dict[str, list[str]], total: int) -> None:
        """Print batch processing summary.

        Args:
            results: Processing results
            total: Total number of artists

        """
        successful = len(results["successful"])
        failed = len(results["failed"])
        skipped = len(results["skipped"])

        self.console_logger.info("\n%s", "=" * 50)
        self.console_logger.info("BATCH PROCESSING SUMMARY")
        self.console_logger.info("=" * 50)
        self.console_logger.info("Total artists: %d", total)
        self.console_logger.info("✅ Successful: %d", successful)

        if failed:
            self.console_logger.error("Failed: %d", failed)
            for artist in results["failed"]:
                self.console_logger.error("  - %s", artist)
        if skipped:
            self.console_logger.warning("Skipped: %d", skipped)
            for artist in results["skipped"]:
                self.console_logger.warning("  - %s", artist)

        # Success rate
        if total > 0:
            success_rate = (successful / total) * 100
            self.console_logger.info("\nSuccess rate: %.1f%%", success_rate)
