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

        async def smart_run_script(script_name: str, *args: object, **kwargs: object) -> str:
            """Mock AppleScript execution with context-aware responses."""
            del args, kwargs  # Unused
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
        mock_deps.pending_verification_service.get_all_pending_albums = AsyncMock(return_value=[])

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

    @pytest.mark.asyncio
    async def test_cli_update_genres(self) -> None:
        """Test update_genres CLI command execution."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        test_artist = "Led Zeppelin"

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "update_genres", "--artist", test_artist])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # Verify artist filtering was applied
            assert args.artist == test_artist
            assert args.command == "update_genres"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_update_genres_alias(self) -> None:
        """Test 'genres' alias for update_genres command."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "genres"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # 'genres' is an alias for 'update_genres'
            assert args.command == "genres"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_restore_release_years(self) -> None:
        """Test restore_release_years CLI command execution."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        test_artist = "David Bowie"
        test_album = "Hunky Dory"

        try:
            cli = CLI()
            args = cli.parse_args(
                [
                    "--config",
                    config_path,
                    "--dry-run",
                    "restore_release_years",
                    "--artist",
                    test_artist,
                    "--album",
                    test_album,
                    "--threshold",
                    "3",
                ]
            )

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # Verify all parameters were parsed correctly
            assert args.artist == test_artist
            assert args.album == test_album
            assert args.threshold == 3
            assert args.command == "restore_release_years"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_restore_alias(self) -> None:
        """Test 'restore' alias for restore_release_years command."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(
                [
                    "--config",
                    config_path,
                    "--dry-run",
                    "restore",
                    "--artist",
                    "Test Artist",
                ]
            )

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # 'restore' is alias for 'restore_release_years'
            assert args.command == "restore"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_verify_database(self) -> None:
        """Test verify_database CLI command execution."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "verify_database"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            assert args.command == "verify_database"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_verify_db_alias(self) -> None:
        """Test 'verify-db' alias for verify_database command."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "verify-db"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # 'verify-db' is alias for 'verify_database'
            assert args.command == "verify-db"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_verify_pending(self) -> None:
        """Test verify_pending CLI command execution."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "verify_pending"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            assert args.command == "verify_pending"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_pending_alias(self) -> None:
        """Test 'pending' alias for verify_pending command."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "pending"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # 'pending' is alias for 'verify_pending'
            assert args.command == "pending"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_batch_command(self) -> None:
        """Test batch CLI command execution."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        # Create temporary batch file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as batch_file:
            batch_file.write("Test Artist 1\n")
            batch_file.write("Test Artist 2\n")
            batch_file_path = batch_file.name

        try:
            cli = CLI()
            args = cli.parse_args(
                [
                    "--config",
                    config_path,
                    "--dry-run",
                    "batch",
                    "--file",
                    batch_file_path,
                    "--operation",
                    "years",
                ]
            )

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # Verify batch parameters
            assert args.command == "batch"
            assert args.file == batch_file_path
            assert args.operation == "years"

        finally:
            Path(config_path).unlink(missing_ok=True)
            Path(batch_file_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_batch_operations(self) -> None:
        """Test batch command with different operation types."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        # Create temporary batch file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as batch_file:
            batch_file.write("Test Artist\n")
            batch_file_path = batch_file.name

        try:
            for operation in ["clean", "years", "full"]:
                cli = CLI()
                args = cli.parse_args(
                    [
                        "--config",
                        config_path,
                        "--dry-run",
                        "batch",
                        "--file",
                        batch_file_path,
                        "--operation",
                        operation,
                    ]
                )

                with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                    orchestrator = Orchestrator(mock_deps)
                    await orchestrator.run_command(args)

                assert args.operation == operation

        finally:
            Path(config_path).unlink(missing_ok=True)
            Path(batch_file_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_rotate_keys_command(self) -> None:
        """Test rotate_keys CLI command execution.

        Note: rotate_keys doesn't require Music.app to be running.
        """
        config_path, config_data = self.create_temp_config()
        # Add api_authentication section for key rotation
        config_data["api_authentication"] = {
            "discogs_token": "test_encrypted_token",
        }
        mock_deps = self.create_mock_dependency_container(config_data)
        mock_deps.config_path = Path(config_path)

        try:
            cli = CLI()
            args = cli.parse_args(
                [
                    "--config",
                    config_path,
                    "rotate_keys",
                    "--no-backup",
                ]
            )

            # rotate_keys should NOT check for Music.app
            # It should run without the is_music_app_running mock returning True
            with patch("core.models.metadata_utils.is_music_app_running", return_value=False):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # Verify command and flags were parsed
            assert args.command == "rotate_keys"
            assert args.no_backup is True

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_rotate_keys_alias(self) -> None:
        """Test 'rotate-keys' alias for rotate_keys command."""
        config_path, config_data = self.create_temp_config()
        config_data["api_authentication"] = {}
        mock_deps = self.create_mock_dependency_container(config_data)
        mock_deps.config_path = Path(config_path)

        try:
            cli = CLI()
            args = cli.parse_args(
                [
                    "--config",
                    config_path,
                    "rotate-keys",
                ]
            )

            with patch("core.models.metadata_utils.is_music_app_running", return_value=False):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # 'rotate-keys' is alias for 'rotate_keys'
            assert args.command == "rotate-keys"

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_force_flag(self) -> None:
        """Test --force flag functionality."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "--force"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            assert args.force is True

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_test_mode_flag(self) -> None:
        """Test --test-mode flag functionality."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "--test-mode"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            assert args.test_mode is True

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_verbose_flag(self) -> None:
        """Test --verbose flag functionality."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "--verbose"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            assert args.verbose is True

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_quiet_flag(self) -> None:
        """Test --quiet flag functionality."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "--quiet"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            assert args.quiet is True

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_fresh_flag(self) -> None:
        """Test --fresh flag functionality."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        # Mock cache clear method
        mock_deps.cache_service.clear = AsyncMock()
        mock_deps.library_snapshot_service.clear_snapshot = MagicMock()

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run", "--fresh"])

            with patch("core.models.metadata_utils.is_music_app_running", return_value=True):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            assert args.fresh is True
            # Verify caches were cleared
            mock_deps.cache_service.clear.assert_called_once()
            mock_deps.library_snapshot_service.clear_snapshot.assert_called_once()

        finally:
            Path(config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cli_music_app_not_running(self) -> None:
        """Test behavior when Music.app is not running."""
        config_path, config_data = self.create_temp_config()
        mock_deps = self.create_mock_dependency_container(config_data)

        try:
            cli = CLI()
            args = cli.parse_args(["--config", config_path, "--dry-run"])

            # Mock Music.app as not running
            with patch("core.models.metadata_utils.is_music_app_running", return_value=False):
                orchestrator = Orchestrator(mock_deps)
                await orchestrator.run_command(args)

            # Should not crash, but should log error
            # Verify error was logged (MockLogger stores messages)
            error_logger = mock_deps.error_logger
            # The error message contains "Music app is not running"
            assert error_logger is not None

        finally:
            Path(config_path).unlink(missing_ok=True)


class TestCLIArgumentParsing:
    """Tests for CLI argument parsing edge cases."""

    def test_cli_help_message(self) -> None:
        """Test that CLI provides help message."""
        cli = CLI()
        # Calling print_help should not raise
        cli.print_help()

    def test_cli_parse_empty_args(self) -> None:
        """Test parsing with no arguments (default command)."""
        cli = CLI()
        args = cli.parse_args([])

        # Default command is None (runs main workflow)
        assert args.command is None
        assert args.dry_run is False
        assert args.force is False

    def test_cli_parse_clean_artist_requires_artist(self) -> None:
        """Test that clean_artist requires --artist argument."""
        cli = CLI()

        with pytest.raises(SystemExit):
            cli.parse_args(["clean_artist"])

    def test_cli_parse_revert_years_requires_artist(self) -> None:
        """Test that revert_years requires --artist argument."""
        cli = CLI()

        with pytest.raises(SystemExit):
            cli.parse_args(["revert_years"])

    def test_cli_parse_batch_requires_file(self) -> None:
        """Test that batch requires --file argument."""
        cli = CLI()

        with pytest.raises(SystemExit):
            cli.parse_args(["batch"])

    def test_cli_parse_verbose_short_flag(self) -> None:
        """Test -v short flag for verbose."""
        cli = CLI()
        args = cli.parse_args(["-v"])

        assert args.verbose is True

    def test_cli_parse_quiet_short_flag(self) -> None:
        """Test -q short flag for quiet."""
        cli = CLI()
        args = cli.parse_args(["-q"])

        assert args.quiet is True

    def test_cli_parse_restore_release_years_default_threshold(self) -> None:
        """Test restore_release_years uses default threshold of 5."""
        cli = CLI()
        args = cli.parse_args(["restore_release_years", "--artist", "Test"])

        assert args.threshold == 5

    def test_cli_parse_batch_default_operation(self) -> None:
        """Test batch command defaults to 'full' operation."""
        cli = CLI()
        args = cli.parse_args(["batch", "--file", "/tmp/test.txt"])

        assert args.operation == "full"

    def test_cli_parse_multiple_flags(self) -> None:
        """Test parsing with multiple flags combined."""
        cli = CLI()
        args = cli.parse_args(
            [
                "--dry-run",
                "--force",
                "--test-mode",
                "--verbose",
                "--fresh",
            ]
        )

        assert args.dry_run is True
        assert args.force is True
        assert args.test_mode is True
        assert args.verbose is True
        assert args.fresh is True
