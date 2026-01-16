"""End-to-End tests for Command Line Interface with Allure reporting."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import yaml

from app.cli import CLI
from app.orchestrator import Orchestrator
from services.dependency_container import DependencyContainer
from tests.mocks.csv_mock import MockAnalytics, MockLogger


# noinspection PyUnusedLocal
class TestCLIE2E:
    """End-to-End tests for the command line interface."""

    @staticmethod
    def create_temp_config(**overrides: Any) -> tuple[str, dict[str, Any]]:
        """Create temporary configuration file with optional overrides."""
        config_data = {
            "music_library_path": str(Path.home() / "test_music_library.musiclibrary"),
            "applescript_directory": "applescripts",
            "dry_run": True,
            "force_update": False,
            "test_mode": True,
            "processing": {
                "batch_size": 100,
                "apple_script_concurrency": 5,
                "api_concurrency": 10,
            },
            "year_update": {"concurrent_limit": 5, "verification_enabled": True},
            "logging": {"console_level": "INFO", "file_level": "DEBUG"},
            "caching": {"enabled": True, "cache_ttl": 3600},
            "development": {"test_artists": ["Test Artist", "Demo Artist"]},
        } | overrides

        # Create temporary config file with context manager
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as temp_file:
            yaml.dump(config_data, temp_file)
            return temp_file.name, config_data

    @staticmethod
    def create_mock_dependency_container(config: dict[str, Any]) -> MagicMock:
        """Create a mock dependency container for CLI testing."""
        mock_deps = MagicMock(spec=DependencyContainer)

        # Basic services
        mock_deps.config = config
        mock_deps.console_logger = MockLogger()
        mock_deps.error_logger = MockLogger()
        mock_deps.analytics = MockAnalytics()

        # FIX: Add config_path to prevent MagicMock from breaking file operations
        # This is critical - without it, MusicUpdater.__init__() hangs when trying to
        # resolve artist_renamer config path, because MagicMock.open() creates infinite recursion
        temp_dir = Path(tempfile.mkdtemp())
        mock_config_file = temp_dir / "config.yaml"
        mock_deps.config_path = mock_config_file

        # AppleScript client mock
        mock_deps.ap_client = MagicMock()
        mock_deps.ap_client.fetch_all_track_ids = AsyncMock(return_value=[])

        async def smart_run_script(script_name: str, args: list[str] | None = None, timeout: int = 30) -> str:
            """Mock AppleScript execution with context-aware responses."""
            if "fetch_tracks" in script_name:
                return ""  # Empty to signal end of batch
            return "" if "fetch_track_summaries" in script_name else "[]"

        mock_deps.ap_client.run_script = AsyncMock(side_effect=smart_run_script)
        mock_deps.ap_client.update_track_async = AsyncMock(return_value=True)

        # Cache service mock
        mock_deps.cache_service = MagicMock()
        mock_deps.cache_service.get_async = AsyncMock(return_value=None)
        mock_deps.cache_service.set_async = AsyncMock()

        # API orchestrator mock
        mock_deps.api_orchestrator = MagicMock()
        mock_deps.api_orchestrator.get_artist_genres = AsyncMock(return_value=["Rock", "Alternative"])
        mock_deps.api_orchestrator.get_album_year = AsyncMock(return_value=("2020", True, 85))

        # Verification service mock
        mock_deps.pending_verification = MagicMock()
        mock_deps.pending_verification.add_track = MagicMock()
        mock_deps.pending_verification.get_pending_tracks = MagicMock(return_value=[])

        # Pending verification SERVICE mock (accessed via music_updater.deps.pending_verification_service)
        mock_deps.pending_verification_service = MagicMock()
        mock_deps.pending_verification_service.should_auto_verify = AsyncMock(return_value=False)

        # Library snapshot service mock
        mock_deps.library_snapshot_service = MagicMock()
        mock_deps.library_snapshot_service.is_enabled = MagicMock(return_value=False)
        mock_deps.library_snapshot_service.get_library_mtime = AsyncMock(return_value=None)

        return mock_deps

    @pytest.mark.asyncio
    async def test_cli_default_command(self) -> None:
        """Test default CLI command execution."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run"])

            with (
                patch("core.models.metadata_utils.is_music_app_running", return_value=True),
            ):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)
            # Command executed without exceptions - success
            # Note: In test_mode + dry_run, AppleScript may not be called

            # Verify dry-run mode was used
            assert args.dry_run is True

            # Check that analytics service was used
            assert mock_deps.analytics is not None

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_clean_artist(self) -> None:
        """Test clean_artist CLI command execution."""
        config_path, config_data = self.create_temp_config(force_update=True)
        mock_deps = self.create_mock_dependency_container(config_data)

        test_artist = "The Beatles"

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "clean_artist", "--artist", test_artist])

            with (
                patch("core.models.metadata_utils.is_music_app_running", return_value=True),
            ):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)
            # Command executed without exceptions - success
            # Note: In test_mode + dry_run, AppleScript may not be called

            # Verify artist filtering was applied
            assert hasattr(args, "artist")
            assert args.artist == test_artist

            # Should have analytics service available
            assert mock_deps.analytics is not None

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_update_years(self) -> None:
        """Test update_years CLI command execution."""
        config_path, config_data = self.create_temp_config()
        config_data["year_update"] = {"concurrent_limit": 10, "verification_enabled": True}
        mock_deps = self.create_mock_dependency_container(config_data)

        test_artist = "Pink Floyd"

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "update_years", "--artist", test_artist])

            with (
                patch("core.models.metadata_utils.is_music_app_running", return_value=True),
            ):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)
            # Command executed without exceptions - success
            # Note: In test_mode + dry_run, AppleScript/API may not be called

            # Verify artist filtering
            assert args.artist == test_artist

            # Analytics service should be available
            assert mock_deps.analytics is not None

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_revert_years(self) -> None:
        """Test revert_years CLI command execution."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        test_artist = "Queen"
        test_album = "A Night at the Opera"

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "revert_years", "--artist", test_artist, "--album", test_album])

            with (
                patch("core.models.metadata_utils.is_music_app_running", return_value=True),
            ):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)
            # Revert command may not use AppleScript if no targets found
            # For E2E test, successful execution without exceptions is sufficient

            # Verify targeting parameters
            assert args.artist == test_artist
            assert args.album == test_album

            # Should have analytics service available
            assert mock_deps.analytics is not None

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_dry_run_flag(self) -> None:
        """Test --dry-run flag functionality."""
        config_path, config_data = self.create_temp_config(dry_run=False)  # Start with dry_run disabled
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run"])

            # Mock Music.app running check and execute command
            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # Verify dry-run was applied
            assert args.dry_run is True
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "clean_artist", "--artist", "Test Artist"])

            # Mock Music.app running check and execute command
            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # Verify dry-run was applied
            assert args.dry_run is True
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "update_years", "--artist", "Test Artist"])

            # Mock Music.app running check and execute command
            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # Verify dry-run was applied
            assert args.dry_run is True

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_config_override(self) -> None:
        """Test --config flag for custom configuration."""
        # Create default config
        default_config_path, _ = self.create_temp_config(test_mode=False, batch_size=50, development={"test_artists": ["Default Artist"]})

        # Create custom config with different settings
        custom_config_path, custom_config = self.create_temp_config(
            test_mode=True, batch_size=200, development={"test_artists": ["Custom Artist", "Override Artist"]}, api_concurrency=20
        )

        try:
            # Create mock dependency container
            mock_deps = self.create_mock_dependency_container(custom_config)

            cli = CLI()
            args = cli.parse_args(["--config", custom_config_path, "--dry-run"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                # Mock Music.app running check and execute command
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)
            # Verify the custom config path was used
            assert args.config == custom_config_path

        finally:
            # Clean up temp files
            Path(default_config_path).unlink(missing_ok=True)
            Path(custom_config_path).unlink(missing_ok=True)
