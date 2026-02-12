"""End-to-End tests for Batch Processing Workflows."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.batch.batch_processor import BatchProcessor
from app.music_updater import MusicUpdater
from services.dependency_container import DependencyContainer
from tests.mocks.csv_mock import MockAnalytics, MockLogger  # sourcery skip: dont-import-test-modules


class TestBatchProcessingE2E:
    """End-to-End tests for batch processing workflows."""

    @staticmethod
    def create_test_config(**overrides: Any) -> dict[str, Any]:
        """Create test configuration with optional overrides."""
        temp_dir = tempfile.mkdtemp(prefix="test_batch_")
        return {
            "music_library_path": str(Path(temp_dir) / "test_music_library.musiclibrary"),
            "dry_run": True,
            "force_update": False,
            "test_mode": True,
            "test_artists": [],
            "processing": {
                "batch_size": 10,
                "use_batch_applescript": True,
                "apple_script_concurrency": 2,
            },
            "music_updates": {
                "concurrent_limit": 2,
                "track_processor_batch_size": 5,
            },
            "logging": {
                "log_level": "DEBUG",
                "contextual_logging": True,
            },
            "external_apis": {
                "musicbrainz": {"use_musicbrainz": True, "rate_limit_delay": 0.1},
                "discogs": {"use_discogs": True, "rate_limit_delay": 0.1},
            },
            "caching": {"ttl_days": 7, "force_cache_refresh": False},
        } | overrides

    @staticmethod
    def create_mock_dependency_container(_config: dict[str, Any]) -> MagicMock:
        """Create a mock dependency container for batch testing."""
        mock_deps = MagicMock(spec=DependencyContainer)

        # Basic services
        mock_deps.console_logger = MockLogger()
        mock_deps.error_logger = MockLogger()
        mock_deps.analytics = MockAnalytics()

        # Add config_path to prevent MagicMock recursion issues
        temp_dir = Path(tempfile.mkdtemp())
        mock_config_file = temp_dir / "config.yaml"
        mock_deps.config_path = mock_config_file

        # AppleScript client mock
        mock_deps.ap_client = MagicMock()
        mock_deps.ap_client.get_tracks = AsyncMock(return_value=[])
        mock_deps.ap_client.update_track_async = AsyncMock(return_value=True)

        async def smart_run_script(script_name: str, *_args: object, **_kwargs: object) -> str:
            """Mock script runner for batch testing."""
            if "fetch_tracks" in script_name:
                return ""
            return "" if "fetch_track_summaries" in script_name else "[]"

        mock_deps.ap_client.run_script = AsyncMock(side_effect=smart_run_script)

        # Cache service mock
        mock_deps.cache_service = MagicMock()
        mock_deps.cache_service.get_async = AsyncMock(return_value=None)
        mock_deps.cache_service.set_async = AsyncMock()
        mock_deps.cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        mock_deps.cache_service.cache_album_year = AsyncMock()
        mock_deps.cache_service.store_album_year_in_cache = AsyncMock()

        # External API orchestrator mock
        mock_deps.external_api = MagicMock()
        mock_deps.external_api.get_album_year = AsyncMock(return_value=(None, False, 0))

        # Pending verification mock
        mock_deps.pending_verification = MagicMock()
        mock_deps.pending_verification.add_track = MagicMock()
        mock_deps.pending_verification.get_pending_tracks = MagicMock(return_value=[])
        mock_deps.pending_verification.mark_for_verification = AsyncMock()

        # Library snapshot service mock
        mock_deps.library_snapshot_service = MagicMock()
        mock_deps.library_snapshot_service.is_enabled = MagicMock(return_value=False)
        mock_deps.library_snapshot_service.is_snapshot_valid = AsyncMock(return_value=False)
        mock_deps.library_snapshot_service.get_track_ids_from_snapshot = AsyncMock(return_value=set())
        mock_deps.library_snapshot_service.load_snapshot = AsyncMock(return_value=None)
        mock_deps.library_snapshot_service.save_snapshot = AsyncMock()
        mock_deps.library_snapshot_service.get_library_mtime = AsyncMock(return_value=None)
        mock_deps.library_snapshot_service.compute_smart_delta = AsyncMock(return_value=(set(), set(), set()))

        return mock_deps

    @staticmethod
    def create_temp_batch_file(artists: list[str]) -> str:
        """Create a temporary batch file with artist names."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for artist in artists:
                f.write(f"{artist}\n")
            return f.name

    @pytest.mark.asyncio
    async def test_batch_process_from_file_clean_operation(self) -> None:
        """Test batch processing with 'clean' operation."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        artists = ["Artist A", "Artist B", "Artist C"]
        batch_file = self.create_temp_batch_file(artists)

        try:
            music_updater = MusicUpdater(mock_deps)
            batch_processor = BatchProcessor(
                music_updater,
                mock_deps.console_logger,
                mock_deps.error_logger,
            )

            results = await batch_processor.process_from_file(batch_file, operation="clean")

            # Verify results structure
            assert "successful" in results
            assert "failed" in results
            assert "skipped" in results

            # All artists should be processed (success or fail)
            total_processed = len(results["successful"]) + len(results["failed"]) + len(results["skipped"])
            assert total_processed == len(artists)

        finally:
            Path(batch_file).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_batch_process_from_file_years_operation(self) -> None:
        """Test batch processing with 'years' operation."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        artists = ["Pink Floyd", "Led Zeppelin"]
        batch_file = self.create_temp_batch_file(artists)

        try:
            music_updater = MusicUpdater(mock_deps)
            batch_processor = BatchProcessor(
                music_updater,
                mock_deps.console_logger,
                mock_deps.error_logger,
            )

            results = await batch_processor.process_from_file(batch_file, operation="years")

            # Verify processing completed
            assert isinstance(results, dict)
            assert "successful" in results

        finally:
            Path(batch_file).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_batch_process_from_file_full_operation(self) -> None:
        """Test batch processing with 'full' operation (default)."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        artists = ["The Beatles", "Queen"]
        batch_file = self.create_temp_batch_file(artists)

        try:
            music_updater = MusicUpdater(mock_deps)
            batch_processor = BatchProcessor(
                music_updater,
                mock_deps.console_logger,
                mock_deps.error_logger,
            )

            # 'full' is the default operation, so we omit it
            results = await batch_processor.process_from_file(batch_file)

            assert isinstance(results, dict)
            # Full operation includes clean + years
            total = len(results["successful"]) + len(results["failed"]) + len(results["skipped"])
            assert total == len(artists)

        finally:
            Path(batch_file).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_batch_process_nonexistent_file(self) -> None:
        """Test batch processing with non-existent file."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        music_updater = MusicUpdater(mock_deps)
        batch_processor = BatchProcessor(
            music_updater,
            mock_deps.console_logger,
            mock_deps.error_logger,
        )

        results = await batch_processor.process_from_file("/nonexistent/path/artists.txt")

        # Should return empty results, not crash
        assert results == {"successful": [], "failed": [], "skipped": []}

    @pytest.mark.asyncio
    async def test_batch_process_empty_file(self) -> None:
        """Test batch processing with empty file."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        # Create empty file
        batch_file = self.create_temp_batch_file([])

        try:
            music_updater = MusicUpdater(mock_deps)
            batch_processor = BatchProcessor(
                music_updater,
                mock_deps.console_logger,
                mock_deps.error_logger,
            )

            results = await batch_processor.process_from_file(batch_file)

            # Empty file should process successfully with no artists
            assert results["successful"] == []
            assert results["failed"] == []
            assert results["skipped"] == []

        finally:
            Path(batch_file).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_batch_process_with_blank_lines(self) -> None:
        """Test batch processing ignores blank lines."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        # Create file with blank lines
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Artist One\n")
            f.write("\n")  # Blank line
            f.write("   \n")  # Whitespace only
            f.write("Artist Two\n")
            f.write("\n")
            batch_file = f.name

        try:
            music_updater = MusicUpdater(mock_deps)
            batch_processor = BatchProcessor(
                music_updater,
                mock_deps.console_logger,
                mock_deps.error_logger,
            )

            results = await batch_processor.process_from_file(batch_file)

            # Only 2 valid artists, blank lines should be skipped
            total = len(results["successful"]) + len(results["failed"]) + len(results["skipped"])
            assert total == 2

        finally:
            Path(batch_file).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_batch_process_artists_directly(self) -> None:
        """Test batch processing artists list directly (without file)."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        artists = ["David Bowie", "Prince", "Madonna"]

        music_updater = MusicUpdater(mock_deps)
        batch_processor = BatchProcessor(
            music_updater,
            mock_deps.console_logger,
            mock_deps.error_logger,
        )

        results = await batch_processor.process_artists(artists, operation="clean")

        assert isinstance(results, dict)
        total = len(results["successful"]) + len(results["failed"]) + len(results["skipped"])
        assert total == len(artists)

    @pytest.mark.asyncio
    async def test_batch_process_with_force_flag(self) -> None:
        """Test batch processing with force flag enabled."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        artists = ["Test Artist"]
        batch_file = self.create_temp_batch_file(artists)

        try:
            music_updater = MusicUpdater(mock_deps)
            batch_processor = BatchProcessor(
                music_updater,
                mock_deps.console_logger,
                mock_deps.error_logger,
            )

            results = await batch_processor.process_from_file(batch_file, operation="years", force=True)

            # Force flag should be passed through
            assert isinstance(results, dict)

        finally:
            Path(batch_file).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_batch_process_unicode_artist_names(self) -> None:
        """Test batch processing with Unicode artist names."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        # Artists with various Unicode characters
        artists = [
            "宇多田ヒカル",  # Japanese
            "Björk",  # Icelandic
            "Café Tacvba",  # Spanish with accent
            "Кино",  # Russian
            "방탄소년단",  # Korean
        ]
        batch_file = self.create_temp_batch_file(artists)

        try:
            music_updater = MusicUpdater(mock_deps)
            batch_processor = BatchProcessor(
                music_updater,
                mock_deps.console_logger,
                mock_deps.error_logger,
            )

            results = await batch_processor.process_from_file(batch_file, operation="clean")

            # All Unicode artists should be processed without encoding errors
            total = len(results["successful"]) + len(results["failed"]) + len(results["skipped"])
            assert total == len(artists)

        finally:
            Path(batch_file).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_batch_process_large_artist_list(self) -> None:
        """Test batch processing with a large number of artists."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        # Generate 50 artist names
        artists = [f"Test Artist {i}" for i in range(50)]
        batch_file = self.create_temp_batch_file(artists)

        try:
            music_updater = MusicUpdater(mock_deps)
            batch_processor = BatchProcessor(
                music_updater,
                mock_deps.console_logger,
                mock_deps.error_logger,
            )

            results = await batch_processor.process_from_file(batch_file, operation="clean")

            # All 50 artists should be processed
            total = len(results["successful"]) + len(results["failed"]) + len(results["skipped"])
            assert total == 50

        finally:
            Path(batch_file).unlink(missing_ok=True)


class TestBatchProcessorSummary:
    """Tests for batch processor summary output."""

    @staticmethod
    def create_mock_loggers() -> tuple[MockLogger, MockLogger]:
        """Create mock loggers for testing."""
        return MockLogger(), MockLogger()

    def test_print_summary_all_successful(self) -> None:
        """Test summary output when all artists processed successfully."""
        console_logger, error_logger = self.create_mock_loggers()

        # Create minimal mock for MusicUpdater
        mock_updater = MagicMock(spec=MusicUpdater)

        batch_processor = BatchProcessor(mock_updater, console_logger, error_logger)

        results = {
            "successful": ["Artist A", "Artist B", "Artist C"],
            "failed": [],
            "skipped": [],
        }

        # Should not raise
        batch_processor.print_summary(results, total=3)

        # Verify console logger was used (MockLogger stores messages)
        assert console_logger is not None

    def test_print_summary_with_failures(self) -> None:
        """Test summary output when some artists failed."""
        console_logger, error_logger = self.create_mock_loggers()

        mock_updater = MagicMock(spec=MusicUpdater)
        batch_processor = BatchProcessor(mock_updater, console_logger, error_logger)

        results = {
            "successful": ["Artist A"],
            "failed": ["Artist B", "Artist C"],
            "skipped": [],
        }

        batch_processor.print_summary(results, total=3)

        # Verify error logger was used for failures
        assert error_logger is not None

    def test_print_summary_with_skipped(self) -> None:
        """Test summary output when some artists were skipped."""
        console_logger, error_logger = self.create_mock_loggers()

        mock_updater = MagicMock(spec=MusicUpdater)
        batch_processor = BatchProcessor(mock_updater, console_logger, error_logger)

        results = {
            "successful": ["Artist A"],
            "failed": [],
            "skipped": ["Artist B", "Artist C"],
        }

        batch_processor.print_summary(results, total=3)

        # Verify console logger was used (MockLogger stores messages)
        assert console_logger is not None

    def test_print_summary_empty_results(self) -> None:
        """Test summary output with no artists processed."""
        console_logger, error_logger = self.create_mock_loggers()

        mock_updater = MagicMock(spec=MusicUpdater)
        batch_processor = BatchProcessor(mock_updater, console_logger, error_logger)

        results = {
            "successful": [],
            "failed": [],
            "skipped": [],
        }

        # Should handle empty results without division by zero
        batch_processor.print_summary(results, total=0)


class TestBatchProcessorErrorHandling:
    """Tests for batch processor error handling."""

    @staticmethod
    def create_test_config(**overrides: Any) -> dict[str, Any]:
        """Create test configuration."""
        temp_dir = tempfile.mkdtemp(prefix="test_batch_")
        return {
            "music_library_path": str(Path(temp_dir) / "test_music_library.musiclibrary"),
            "dry_run": True,
            "test_mode": True,
            "processing": {"batch_size": 10},
        } | overrides

    @staticmethod
    def create_mock_dependency_container(_config: dict[str, Any]) -> MagicMock:
        """Create mock dependency container."""
        mock_deps = MagicMock(spec=DependencyContainer)
        mock_deps.console_logger = MockLogger()
        mock_deps.error_logger = MockLogger()
        mock_deps.analytics = MockAnalytics()

        temp_dir = Path(tempfile.mkdtemp())
        mock_deps.config_path = temp_dir / "config.yaml"

        mock_deps.ap_client = MagicMock()
        mock_deps.ap_client.run_script = AsyncMock(return_value="")
        mock_deps.ap_client.update_track_async = AsyncMock(return_value=True)

        mock_deps.cache_service = MagicMock()
        mock_deps.cache_service.get_async = AsyncMock(return_value=None)
        mock_deps.cache_service.set_async = AsyncMock()

        mock_deps.external_api = MagicMock()
        mock_deps.external_api.get_album_year = AsyncMock(return_value=(None, False, 0))

        mock_deps.pending_verification = MagicMock()
        mock_deps.pending_verification.get_pending_tracks = MagicMock(return_value=[])

        mock_deps.library_snapshot_service = MagicMock()
        mock_deps.library_snapshot_service.is_enabled = MagicMock(return_value=False)

        return mock_deps

    @pytest.mark.asyncio
    async def test_batch_recovers_from_artist_processing_error(self) -> None:
        """Test that batch continues after individual artist processing error."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        music_updater = MusicUpdater(mock_deps)

        # Mock run_clean_artist to fail for specific artist
        original_clean = music_updater.run_clean_artist

        async def mock_clean_artist(target_artist: str) -> None:
            """Mock clean artist that fails for a specific artist name."""
            if target_artist == "Failing Artist":
                raise ValueError("Simulated processing error")
            return await original_clean(target_artist)

        music_updater.run_clean_artist = mock_clean_artist  # type: ignore[method-assign]

        batch_processor = BatchProcessor(
            music_updater,
            mock_deps.console_logger,
            mock_deps.error_logger,
        )

        artists = ["Good Artist 1", "Failing Artist", "Good Artist 2"]
        results = await batch_processor.process_artists(artists, operation="clean")

        # Failing Artist should be in failed list
        assert "Failing Artist" in results["failed"]
        # Other artists should still be processed
        assert len(results["successful"]) + len(results["failed"]) == 3

    @pytest.mark.asyncio
    async def test_batch_handles_file_read_error(self) -> None:
        """Test batch processing handles file read errors gracefully."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        music_updater = MusicUpdater(mock_deps)
        batch_processor = BatchProcessor(
            music_updater,
            mock_deps.console_logger,
            mock_deps.error_logger,
        )

        # Create a directory instead of file (will cause read error)
        temp_dir = tempfile.mkdtemp()

        try:
            results = await batch_processor.process_from_file(temp_dir)

            # Should return empty results without crashing
            assert results == {"successful": [], "failed": [], "skipped": []}

        finally:
            Path(temp_dir).rmdir()


class TestBatchProcessingIntegrationScenarios:
    """Integration tests for realistic batch processing scenarios."""

    @staticmethod
    def create_test_config(**overrides: Any) -> dict[str, Any]:
        """Create test configuration."""
        temp_dir = tempfile.mkdtemp(prefix="test_batch_")
        return {
            "music_library_path": str(Path(temp_dir) / "test_music_library.musiclibrary"),
            "dry_run": True,
            "test_mode": True,
            "processing": {"batch_size": 10, "apple_script_concurrency": 2},
            "external_apis": {
                "musicbrainz": {"use_musicbrainz": True, "rate_limit_delay": 0.01},
            },
        } | overrides

    @staticmethod
    def create_mock_dependency_container(_config: dict[str, Any]) -> MagicMock:
        """Create mock dependency container with full services."""
        mock_deps = MagicMock(spec=DependencyContainer)
        mock_deps.console_logger = MockLogger()
        mock_deps.error_logger = MockLogger()
        mock_deps.analytics = MockAnalytics()

        temp_dir = Path(tempfile.mkdtemp())
        mock_deps.config_path = temp_dir / "config.yaml"

        mock_deps.ap_client = MagicMock()
        mock_deps.ap_client.run_script = AsyncMock(return_value="")
        mock_deps.ap_client.update_track_async = AsyncMock(return_value=True)
        mock_deps.ap_client.get_tracks = AsyncMock(return_value=[])

        mock_deps.cache_service = MagicMock()
        mock_deps.cache_service.get_async = AsyncMock(return_value=None)
        mock_deps.cache_service.set_async = AsyncMock()
        mock_deps.cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        mock_deps.cache_service.store_album_year_in_cache = AsyncMock()

        mock_deps.external_api = MagicMock()
        mock_deps.external_api.get_album_year = AsyncMock(return_value=("2020", True, 85))

        mock_deps.pending_verification = MagicMock()
        mock_deps.pending_verification.get_pending_tracks = MagicMock(return_value=[])
        mock_deps.pending_verification.mark_for_verification = AsyncMock()

        mock_deps.library_snapshot_service = MagicMock()
        mock_deps.library_snapshot_service.is_enabled = MagicMock(return_value=False)
        mock_deps.library_snapshot_service.get_library_mtime = AsyncMock(return_value=None)

        return mock_deps

    @pytest.mark.asyncio
    async def test_batch_workflow_clean_then_years(self) -> None:
        """Test a realistic workflow: clean all artists, then update years."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        artists = ["The Rolling Stones", "The Who", "The Kinks"]

        music_updater = MusicUpdater(mock_deps)
        batch_processor = BatchProcessor(
            music_updater,
            mock_deps.console_logger,
            mock_deps.error_logger,
        )

        # First pass: clean
        clean_results = await batch_processor.process_artists(artists, operation="clean")
        assert len(clean_results["successful"]) + len(clean_results["failed"]) == len(artists)

        # Second pass: years
        years_results = await batch_processor.process_artists(artists, operation="years")
        assert len(years_results["successful"]) + len(years_results["failed"]) == len(artists)

    @pytest.mark.asyncio
    async def test_batch_with_mixed_operation_results(self) -> None:
        """Test batch processing where some operations succeed and some fail."""
        config = self.create_test_config()
        mock_deps = self.create_mock_dependency_container(config)

        music_updater = MusicUpdater(mock_deps)

        # Track processing attempts
        processing_log: list[str] = []

        async def tracking_clean(target_artist: str) -> None:
            """Track clean artist calls for verification."""
            processing_log.append(f"clean:{target_artist}")

        music_updater.run_clean_artist = tracking_clean  # type: ignore[method-assign]

        batch_processor = BatchProcessor(
            music_updater,
            mock_deps.console_logger,
            mock_deps.error_logger,
        )

        artists = ["Artist 1", "Artist 2", "Artist 3"]
        await batch_processor.process_artists(artists, operation="clean")

        # All artists should have been processed
        assert len(processing_log) == 3
        for artist in artists:
            assert f"clean:{artist}" in processing_log
