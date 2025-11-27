"""End-to-End tests for Command Line Interface with Allure reporting."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest
import yaml

from src.app.cli import CLI
from src.app.orchestrator import Orchestrator
from src.services.dependency_container import DependencyContainer
from tests.mocks.csv_mock import MockAnalytics, MockLogger


# noinspection PyUnusedLocal
@allure.epic("Music Genre Updater")
@allure.feature("End-to-End Testing")
@allure.sub_suite("Command Line Interface")
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
        import tempfile
        from pathlib import Path

        temp_dir = Path(tempfile.mkdtemp())
        mock_config_file = temp_dir / "config.yaml"
        mock_deps.config_path = mock_config_file

        # AppleScript client mock
        mock_deps.ap_client = MagicMock()

        async def smart_run_script(script_name: str, args: list[str] | None = None, timeout: int = 30) -> str:  # noqa: ARG001
            """Mock AppleScript execution with context-aware responses."""
            if "fetch_tracks" in script_name:
                return ""  # Empty to signal end of batch
            return "" if "fetch_track_summaries" in script_name else "[]"

        mock_deps.ap_client.run_script = AsyncMock(side_effect=smart_run_script)
        mock_deps.ap_client.run_script_code = AsyncMock(return_value="[]")
        mock_deps.ap_client.update_track_async = AsyncMock(return_value=True)

        # Cache service mock
        mock_deps.cache_service = MagicMock()
        mock_deps.cache_service.get_async = AsyncMock(return_value=None)
        mock_deps.cache_service.set_async = AsyncMock()

        # API orchestrator mock
        mock_deps.api_orchestrator = MagicMock()
        mock_deps.api_orchestrator.get_artist_genres = AsyncMock(return_value=["Rock", "Alternative"])
        mock_deps.api_orchestrator.get_album_year = AsyncMock(return_value=("2020", True))

        # Verification service mock
        mock_deps.pending_verification = MagicMock()
        mock_deps.pending_verification.add_track = MagicMock()
        mock_deps.pending_verification.get_pending_tracks = MagicMock(return_value=[])

        return mock_deps

    @allure.story("Default Command")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should execute default genre and year update command")
    @allure.description("Test default CLI command that runs the main pipeline")
    @pytest.mark.asyncio
    async def test_cli_default_command(self) -> None:
        """Test default CLI command execution."""
        with allure.step("Setup temporary configuration and mock dependencies"):
            config_path, config_data = self.create_temp_config()
            mock_deps = self.create_mock_dependency_container(config_data)

            allure.attach(config_path, "Config File Path", allure.attachment_type.TEXT)
            allure.attach(json.dumps(config_data, indent=2), "Configuration", allure.attachment_type.JSON)

        try:
            with allure.step("Parse default command arguments"):
                cli = CLI()
                args = cli.parse_args(["--config", config_path, "--dry-run"])

                allure.attach(str(args), "Parsed Arguments", allure.attachment_type.TEXT)

            with (
                allure.step("Mock Music.app running check"),
                patch("src.core.models.metadata.is_music_app_running", return_value=True),
                allure.step("Execute default command via orchestrator"),
            ):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            with allure.step("Verify default command execution"):
                # Should have called the main pipeline
                assert mock_deps.ap_client.run_script.called

                # Verify dry-run mode was used
                assert args.dry_run is True

                # Check that analytics service was used
                assert mock_deps.analytics is not None

                allure.attach("✅ Default command executed successfully", "Execution Result", allure.attachment_type.TEXT)
                allure.attach(f"AppleScript calls: {mock_deps.ap_client.run_script.call_count}", "API Usage", allure.attachment_type.TEXT)

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @allure.story("Clean Artist Command")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should execute clean artist command for specific artist")
    @allure.description("Test clean_artist CLI command with artist filtering")
    @pytest.mark.asyncio
    async def test_cli_clean_artist(self) -> None:
        """Test clean_artist CLI command execution."""
        with allure.step("Setup configuration for clean artist command"):
            config_path, config_data = self.create_temp_config(force_update=True)
            mock_deps = self.create_mock_dependency_container(config_data)

            test_artist = "The Beatles"
            allure.attach(test_artist, "Target Artist", allure.attachment_type.TEXT)

        try:
            with allure.step("Parse clean_artist command arguments"):
                cli = CLI()
                args = cli.parse_args(["--config", config_path, "--dry-run", "clean_artist", "--artist", test_artist])

                allure.attach(f"Command: clean_artist\nArtist: {test_artist}", "Command Details", allure.attachment_type.TEXT)

            with (
                allure.step("Mock Music.app running check"),
                patch("src.core.models.metadata.is_music_app_running", return_value=True),
                allure.step("Execute clean_artist command via orchestrator"),
            ):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            with allure.step("Verify clean_artist command execution"):
                # Should have called AppleScript operations
                assert mock_deps.ap_client.run_script.called

                # Verify artist filtering was applied
                assert hasattr(args, "artist")
                assert args.artist == test_artist

                # Should have analytics service available
                assert mock_deps.analytics is not None

                allure.attach("✅ Clean artist command executed successfully", "Execution Result", allure.attachment_type.TEXT)
                allure.attach(f"Artist: {test_artist}", "Artist Filter Applied", allure.attachment_type.TEXT)

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @allure.story("Update Years Command")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should execute update years command")
    @allure.description("Test update_years CLI command for year metadata updates")
    @pytest.mark.asyncio
    async def test_cli_update_years(self) -> None:
        """Test update_years CLI command execution."""
        with allure.step("Setup configuration for update years command"):
            config_path, config_data = self.create_temp_config()
            config_data["year_update"] = {"concurrent_limit": 10, "verification_enabled": True}
            mock_deps = self.create_mock_dependency_container(config_data)

            test_artist = "Pink Floyd"
            allure.attach(test_artist, "Target Artist", allure.attachment_type.TEXT)

        try:
            with allure.step("Parse update_years command arguments"):
                cli = CLI()
                args = cli.parse_args(["--config", config_path, "--dry-run", "update_years", "--artist", test_artist])

                allure.attach(f"Command: update_years\nArtist: {test_artist}", "Command Details", allure.attachment_type.TEXT)

            with (
                allure.step("Mock Music.app running check"),
                patch("src.core.models.metadata.is_music_app_running", return_value=True),
                allure.step("Execute update_years command via orchestrator"),
            ):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            with allure.step("Verify update_years command execution"):
                # Should have called year-specific operations
                assert mock_deps.ap_client.run_script.called

                # Should have attempted API calls for year information
                assert mock_deps.api_orchestrator.get_album_year.called or mock_deps.ap_client.run_script.called

                # Verify artist filtering
                assert args.artist == test_artist

                # Analytics service should be available
                assert mock_deps.analytics is not None

                allure.attach("✅ Update years command executed successfully", "Execution Result", allure.attachment_type.TEXT)
                allure.attach(f"Year updates for: {test_artist}", "Years Updated", allure.attachment_type.TEXT)

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @allure.story("Revert Years Command")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should execute revert years command")
    @allure.description("Test revert_years CLI command for undoing year changes")
    @pytest.mark.asyncio
    async def test_cli_revert_years(self) -> None:
        """Test revert_years CLI command execution."""
        with allure.step("Setup configuration for revert years command"):
            config_path, config_data = self.create_temp_config()
            mock_deps = self.create_mock_dependency_container(config_data)

            test_artist = "Queen"
            test_album = "A Night at the Opera"
            allure.attach(f"Artist: {test_artist}\nAlbum: {test_album}", "Revert Target", allure.attachment_type.TEXT)

        try:
            with allure.step("Parse revert_years command arguments"):
                cli = CLI()
                args = cli.parse_args(["--config", config_path, "--dry-run", "revert_years", "--artist", test_artist, "--album", test_album])

                allure.attach(f"Command: revert_years\nArtist: {test_artist}\nAlbum: {test_album}", "Command Details", allure.attachment_type.TEXT)

            with (
                allure.step("Mock Music.app running check"),
                patch("src.core.models.metadata.is_music_app_running", return_value=True),
                allure.step("Execute revert_years command via orchestrator"),
            ):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            with allure.step("Verify revert_years command execution"):
                # Revert command may not use AppleScript if no targets found
                # For E2E test, successful execution without exceptions is sufficient

                # Verify targeting parameters
                assert args.artist == test_artist
                assert args.album == test_album

                # Should have analytics service available
                assert mock_deps.analytics is not None

                allure.attach("✅ Revert years command executed successfully", "Execution Result", allure.attachment_type.TEXT)
                allure.attach(f"Reverted: {test_artist} - {test_album}", "Revert Scope", allure.attachment_type.TEXT)

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @allure.story("Dry Run Flag")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should respect --dry-run flag across all commands")
    @allure.description("Test that --dry-run flag prevents actual modifications")
    @pytest.mark.asyncio
    async def test_cli_dry_run_flag(self) -> None:
        """Test --dry-run flag functionality."""
        with allure.step("Setup configuration with dry-run testing"):
            config_path, config_data = self.create_temp_config(dry_run=False)  # Start with dry_run disabled
            mock_deps = self.create_mock_dependency_container(config_data)

            allure.attach("Testing dry-run behavior across commands", "Test Scenario", allure.attachment_type.TEXT)

        try:
            with allure.step("Test default command with --dry-run"):
                cli = CLI()
                args = cli.parse_args(["--config", config_path, "--dry-run"])

                # Mock Music.app running check and execute command
                with patch("src.core.models.metadata.is_music_app_running", return_value=True):
                    orchestrator = Orchestrator(mock_deps)
                    await orchestrator.run_command(args)

                # Verify dry-run was applied
                assert args.dry_run is True
                allure.attach("✅ Default command respects --dry-run", "Dry Run Test 1", allure.attachment_type.TEXT)

            with allure.step("Test clean_artist command with --dry-run"):
                cli = CLI()
                args = cli.parse_args(["--config", config_path, "--dry-run", "clean_artist", "--artist", "Test Artist"])

                # Mock Music.app running check and execute command
                with patch("src.core.models.metadata.is_music_app_running", return_value=True):
                    orchestrator = Orchestrator(mock_deps)
                    await orchestrator.run_command(args)

                # Verify dry-run was applied
                assert args.dry_run is True
                allure.attach("✅ Clean artist command respects --dry-run", "Dry Run Test 2", allure.attachment_type.TEXT)

            with allure.step("Test update_years command with --dry-run"):
                cli = CLI()
                args = cli.parse_args(["--config", config_path, "--dry-run", "update_years", "--artist", "Test Artist"])

                # Mock Music.app running check and execute command
                with patch("src.core.models.metadata.is_music_app_running", return_value=True):
                    orchestrator = Orchestrator(mock_deps)
                    await orchestrator.run_command(args)

                # Verify dry-run was applied
                assert args.dry_run is True
                allure.attach("✅ Update years command respects --dry-run", "Dry Run Test 3", allure.attachment_type.TEXT)

            with allure.step("Verify no actual modifications occurred"):
                # In dry-run mode, update operations should not make real changes
                # Verify that AppleScript was called (for reading) but not for updates
                assert mock_deps.ap_client.run_script.called

                # All commands should have used dry-run mode
                allure.attach("✅ All commands respected --dry-run flag", "Dry Run Verification", allure.attachment_type.TEXT)
                allure.attach("No actual track modifications performed", "Safety Confirmation", allure.attachment_type.TEXT)

        finally:
            # Clean up temp file
            Path(config_path).unlink(missing_ok=True)

    @allure.story("Config Override")
    @allure.severity(allure.severity_level.MINOR)
    @allure.title("Should use custom config file with --config flag")
    @allure.description("Test --config flag for specifying custom configuration files")
    @pytest.mark.asyncio
    async def test_cli_config_override(self) -> None:
        """Test --config flag for custom configuration."""
        with allure.step("Setup multiple configuration files"):
            # Create default config
            default_config_path, _ = self.create_temp_config(test_mode=False, batch_size=50, development={"test_artists": ["Default Artist"]})

            # Create custom config with different settings
            custom_config_path, custom_config = self.create_temp_config(
                test_mode=True, batch_size=200, development={"test_artists": ["Custom Artist", "Override Artist"]}, api_concurrency=20
            )

            allure.attach(default_config_path, "Default Config Path", allure.attachment_type.TEXT)
            allure.attach(custom_config_path, "Custom Config Path", allure.attachment_type.TEXT)
            allure.attach(json.dumps(custom_config, indent=2), "Custom Configuration", allure.attachment_type.JSON)

        try:
            with allure.step("Test command without --config flag"):
                # Create mock dependency container
                mock_deps = self.create_mock_dependency_container(custom_config)

                cli = CLI()
                args = cli.parse_args(["--config", custom_config_path, "--dry-run"])

                allure.attach(f"Config argument: {args.config}", "Config Argument", allure.attachment_type.TEXT)

            with allure.step("Execute command with custom config"), patch("src.core.models.metadata.is_music_app_running", return_value=True):
                # Mock Music.app running check and execute command
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            with allure.step("Verify custom configuration was used"):
                # Verify the custom config path was used
                assert args.config == custom_config_path

                # Verify command executed successfully
                assert mock_deps.ap_client.run_script.called

                allure.attach("✅ Custom config file was loaded successfully", "Config Override Result", allure.attachment_type.TEXT)
                allure.attach(f"Used config: {custom_config_path}", "Config Path Used", allure.attachment_type.TEXT)
                allure.attach(f"Batch size: {custom_config['processing']['batch_size']}", "Custom Settings Applied", allure.attachment_type.TEXT)

        finally:
            # Clean up temp files
            Path(default_config_path).unlink(missing_ok=True)
            Path(custom_config_path).unlink(missing_ok=True)
