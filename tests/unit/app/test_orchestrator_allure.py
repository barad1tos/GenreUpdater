"""Enhanced Orchestrator tests with Allure reporting."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import allure
import pytest
from app.music_updater import MusicUpdater
from app.orchestrator import Orchestrator

_TEST_PASSWORD = "test-password"  # noqa: S105 - test-only credential placeholder


@allure.epic("Music Genre Updater")
@allure.feature("Orchestration")
class TestOrchestratorAllure:
    """Enhanced tests for Orchestrator with Allure reporting."""

    @staticmethod
    def create_mock_deps() -> Mock:
        """Create a mock DependencyContainer."""
        deps = Mock()
        deps.config = {"development": {"test_artists": ["Test Artist 1", "Test Artist 2"]}}
        deps.console_logger = Mock()
        deps.error_logger = Mock()
        deps.config_path = Path("/test/config.yaml")
        deps.analytics = Mock()
        deps.ap_client = Mock()
        deps.cache_service = Mock()
        deps.dry_run = False
        deps.api_client = Mock()
        deps.database_verifier = Mock()
        deps.run_tracking_manager = Mock()
        deps.csv_manager = Mock()
        deps.external_api_orchestrator = Mock()
        deps.incremental_filter_service = Mock()
        deps.year_retriever = Mock()
        deps.pending_verifier = Mock()
        return deps

    @staticmethod
    def create_mock_args(
        command: str | None = None,
        dry_run: bool = False,
        force: bool = False,
        test_mode: bool = False,
        fresh: bool = False,
        **kwargs: Any,
    ) -> argparse.Namespace:
        """Create mock command-line arguments."""
        args = argparse.Namespace()
        args.command = command
        args.dry_run = dry_run
        args.force = force
        args.test_mode = test_mode
        args.fresh = fresh

        # Add any additional keyword arguments
        for key, value in kwargs.items():
            setattr(args, key, value)

        return args

    @allure.story("Initialization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should initialize Orchestrator with dependencies")
    @allure.description("Test that Orchestrator initializes correctly with required dependencies")
    def test_orchestrator_initialization(self) -> None:
        """Test Orchestrator initialization."""
        with allure.step("Create mock dependencies"):
            deps = self.create_mock_deps()

        with allure.step("Initialize Orchestrator"):
            orchestrator = Orchestrator(deps)

        with allure.step("Verify initialization"):
            assert orchestrator.deps is deps
            assert isinstance(orchestrator.music_updater, MusicUpdater)
            assert orchestrator.config == deps.config
            assert orchestrator.console_logger == deps.console_logger
            assert orchestrator.error_logger == deps.error_logger

            allure.attach("Orchestrator initialized successfully", "Result", allure.attachment_type.TEXT)

    @allure.story("Music App Check")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should check if Music app is required for commands")
    @allure.description("Test that orchestrator correctly identifies commands requiring Music.app")
    @pytest.mark.parametrize(
        ("command", "requires_music"),
        [
            ("clean_artist", True),
            ("update_years", True),
            ("revert_years", True),
            ("verify_database", True),
            ("batch", True),
            ("rotate_keys", False),
            ("rotate-keys", False),
            (None, True),  # Default command
        ],
    )
    def test_requires_music_app(self, command: str | None, requires_music: bool) -> None:
        """Test _requires_music_app method."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)

        with allure.step(f"Check if '{command}' requires Music app"):
            result = orchestrator._requires_music_app(command)

        with allure.step("Verify result"):
            assert result == requires_music
            allure.attach(f"Command: {command}, Requires Music: {result}", "Check Result", allure.attachment_type.TEXT)

    @allure.story("Command Routing")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle Music app not running")
    @allure.description("Test that orchestrator stops execution when Music app is not running")
    @pytest.mark.asyncio
    async def test_music_app_not_running(self) -> None:
        """Test handling when Music app is not running."""
        with allure.step("Setup orchestrator and mock"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            args = self.create_mock_args(command="clean_artist", artist="Test Artist")

        with allure.step("Mock Music app as not running"), patch("app.orchestrator.is_music_app_running", return_value=False):
            await orchestrator.run_command(args)

        with allure.step("Verify error message"):
            console_error_mock = cast(Mock, orchestrator.console_logger.error)
            console_error_mock.assert_called_once_with("Music app is not running! Please start Music.app before running this script.")
            allure.attach("Music app check prevented execution", "Result", allure.attachment_type.TEXT)

    @allure.story("Dry Run Mode")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should set dry-run context correctly")
    @allure.description("Test dry-run and test mode context setting")
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("dry_run", "test_mode", "expected_mode"),
        [
            (True, False, "dry_run"),
            (False, True, "test"),
            (True, True, "test"),  # test_mode takes precedence
        ],
    )
    async def test_dry_run_context(self, dry_run: bool, test_mode: bool, expected_mode: str) -> None:
        """Test dry-run context setting."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            orchestrator.music_updater = Mock(spec=MusicUpdater)
            orchestrator.music_updater.run_main_pipeline = AsyncMock()
            orchestrator.music_updater.set_dry_run_context = Mock()
            orchestrator.music_updater.database_verifier = Mock()
            orchestrator.music_updater.database_verifier.should_auto_verify = AsyncMock(return_value=False)

            args = self.create_mock_args(dry_run=dry_run, test_mode=test_mode)

        with allure.step("Run command with dry-run/test mode"), patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)

        with allure.step("Verify dry-run context was set"):
            orchestrator.music_updater.set_dry_run_context.assert_called_once()
            set_context_mock = orchestrator.music_updater.set_dry_run_context
            mode_arg, artists_arg = set_context_mock.call_args.args
            assert mode_arg == expected_mode
            assert artists_arg == {"Test Artist 1", "Test Artist 2"}

            allure.attach(f"Mode: {expected_mode}", "Dry Run Context", allure.attachment_type.TEXT)

    @allure.story("Command Execution")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should route to clean artist command")
    @allure.description("Test clean artist command execution")
    @pytest.mark.asyncio
    async def test_clean_artist_command(self) -> None:
        """Test clean artist command routing."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            orchestrator.music_updater = Mock(spec=MusicUpdater)
            orchestrator.music_updater.run_clean_artist = AsyncMock()

            args = self.create_mock_args(command="clean_artist", artist="Test Artist", force=True)

        with allure.step("Execute clean artist command"), patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)

        with allure.step("Verify clean artist was called with only artist kwarg"):
            orchestrator.music_updater.run_clean_artist.assert_called_once()
            run_clean_artist_mock = orchestrator.music_updater.run_clean_artist
            clean_kwargs = run_clean_artist_mock.call_args.kwargs
            # run_clean_artist should only receive the artist kwarg and no force flag
            assert clean_kwargs == {"artist": "Test Artist"}
            allure.attach("Clean artist executed (no force)", "Command Result", allure.attachment_type.TEXT)

    @allure.story("Command Execution")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should route to update years command")
    @allure.description("Test update years command execution")
    @pytest.mark.asyncio
    async def test_update_years_command(self) -> None:
        """Test update years command routing."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            orchestrator.music_updater = Mock(spec=MusicUpdater)
            orchestrator.music_updater.run_update_years = AsyncMock()

            args = self.create_mock_args(command="update_years", artist="Test Artist", force=True)

        with allure.step("Execute update years command"), patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)

        with allure.step("Verify update years was called"):
            orchestrator.music_updater.run_update_years.assert_called_once()
            update_kwargs = orchestrator.music_updater.run_update_years.call_args.kwargs
            assert update_kwargs["artist"] == "Test Artist"
            assert update_kwargs["force"] is True
            allure.attach("Update years executed", "Command Result", allure.attachment_type.TEXT)

    @allure.story("Command Execution")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should route to revert years command")
    @allure.description("Test revert years command execution")
    @pytest.mark.asyncio
    async def test_revert_years_command(self) -> None:
        """Test revert years command routing."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            orchestrator.music_updater = Mock(spec=MusicUpdater)
            orchestrator.music_updater.run_revert_years = AsyncMock()

            args = self.create_mock_args(command="revert_years", artist="Test Artist", album="Test Album", backup_csv="backup.csv")

        with allure.step("Execute revert years command"), patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)

        with allure.step("Verify revert years was called"):
            orchestrator.music_updater.run_revert_years.assert_called_once()
            revert_kwargs = orchestrator.music_updater.run_revert_years.call_args.kwargs
            assert revert_kwargs["artist"] == "Test Artist"
            assert revert_kwargs["album"] == "Test Album"
            assert revert_kwargs["backup_csv"] == "backup.csv"
            allure.attach("Revert years executed", "Command Result", allure.attachment_type.TEXT)

    @allure.story("Command Execution")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should route to verify database command")
    @allure.description("Test verify database command execution")
    @pytest.mark.asyncio
    async def test_verify_database_command(self) -> None:
        """Test verify database command routing."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            orchestrator.music_updater = Mock(spec=MusicUpdater)
            orchestrator.music_updater.run_verify_database = AsyncMock()

            args = self.create_mock_args(command="verify_database", force=True)

        with allure.step("Execute verify database command"), patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)

        with allure.step("Verify database verification was called"):
            orchestrator.music_updater.run_verify_database.assert_called_once()
            verify_kwargs = orchestrator.music_updater.run_verify_database.call_args.kwargs
            assert verify_kwargs["force"] is True
            allure.attach("Database verification executed", "Command Result", allure.attachment_type.TEXT)

    @allure.story("Command Execution")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should route to main pipeline when no command")
    @allure.description("Test default main pipeline execution")
    @pytest.mark.asyncio
    async def test_main_pipeline_default(self) -> None:
        """Test main pipeline execution when no command specified."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            orchestrator.music_updater = Mock(spec=MusicUpdater)
            orchestrator.music_updater.run_main_pipeline = AsyncMock()
            orchestrator.music_updater.set_dry_run_context = Mock()
            orchestrator.music_updater.database_verifier = Mock()
            orchestrator.music_updater.database_verifier.should_auto_verify = AsyncMock(return_value=False)

            args = self.create_mock_args()

        with allure.step("Execute default command"), patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)

        with allure.step("Verify main pipeline was called"):
            orchestrator.music_updater.run_main_pipeline.assert_called_once()
            pipeline_kwargs = orchestrator.music_updater.run_main_pipeline.call_args.kwargs
            assert pipeline_kwargs.get("force", False) is False
            allure.attach("Main pipeline executed", "Command Result", allure.attachment_type.TEXT)

    @allure.story("Batch Processing")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should route to batch processing")
    @allure.description("Test batch processing command execution")
    @pytest.mark.asyncio
    async def test_batch_command(self) -> None:
        """Test batch processing command."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)

            args = self.create_mock_args(
                command="batch",
                file="batch.txt",
                operation="full",
                force=True,
            )

        with (
            allure.step("Mock BatchProcessor"),
            patch("app.orchestrator.is_music_app_running", return_value=True),
            patch("app.orchestrator.BatchProcessor") as mock_batch_processor_class,
        ):
            mock_batch_processor = Mock()
            mock_batch_processor.process_from_file = AsyncMock()
            mock_batch_processor_class.return_value = mock_batch_processor

            await orchestrator.run_command(args)

        with allure.step("Verify batch processor was called"):
            mock_batch_processor_class.assert_called_once_with(
                orchestrator.music_updater,
                orchestrator.console_logger,
                orchestrator.error_logger,
            )
            mock_batch_processor.process_from_file.assert_called_once()
            batch_call_kwargs = mock_batch_processor.process_from_file.call_args.kwargs
            assert batch_call_kwargs["file_path"] == "batch.txt"
            assert batch_call_kwargs["operation"] == "full"
            assert batch_call_kwargs["force"] is True
            allure.attach("Batch processing executed", "Command Result", allure.attachment_type.TEXT)

    @allure.story("Key Rotation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle key rotation command")
    @allure.description("Test encryption key rotation functionality")
    def test_rotate_keys_command(self) -> None:
        """Test rotate keys command."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            deps.config_path = Path("/test/config.yaml")
            orchestrator = Orchestrator(deps)

            args = self.create_mock_args(
                command="rotate_keys",
                new_password=_TEST_PASSWORD,
                no_backup=False,
            )

        mock_secure_config = Mock()
        with (
            allure.step("Mock SecureConfig and file operations"),
            patch("app.orchestrator.SecureConfig", return_value=mock_secure_config),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", MagicMock()),
            patch(
                "yaml.safe_load",
                return_value={
                    "api_authentication": {
                        "discogs_token": "encrypted_token",
                        "lastfm_api_key": "encrypted_key",
                    }
                },
            ),
            patch("shutil.copy2"),
        ):
            mock_secure_config.key_file_path = "/test/key.pem"
            mock_secure_config.is_token_encrypted.return_value = True
            mock_secure_config.decrypt_token.return_value = "decrypted_token"
            mock_secure_config.encrypt_token.return_value = "encrypted_token"
            mock_secure_config.rotate_key = Mock()
            mock_secure_config.get_secure_config_status.return_value = {
                "key_file_path": "/test/key.pem",
                "encryption_initialized": True,
                "password_configured": True,
            }
            orchestrator._run_rotate_encryption_keys(args)

        with allure.step("Verify key rotation steps executed"):
            mock_secure_config.rotate_key.assert_called_once_with(_TEST_PASSWORD)
            console_info_mock = cast(Mock, orchestrator.console_logger.info)
            console_info_mock.assert_any_call("✅ Encryption key rotation completed (placeholder mode)")
            allure.attach("Key rotation executed successfully", "Result", allure.attachment_type.TEXT)

    @allure.story("Key Rotation")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle key rotation errors gracefully")
    @allure.description("Test error handling during key rotation")
    def test_rotate_keys_error_handling(self) -> None:
        """Test error handling in key rotation."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            deps.config_path = Path("/test/config.yaml")
            orchestrator = Orchestrator(deps)

            args = self.create_mock_args(command="rotate_keys")

        with (
            allure.step("Mock SecureConfig to raise error"),
            patch("app.orchestrator.SecureConfig") as mock_secure_config_class,
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", side_effect=OSError("File error")),
        ):
            mock_secure_config = Mock()
            mock_secure_config_class.return_value = mock_secure_config

            orchestrator._run_rotate_encryption_keys(args)

        with allure.step("Verify error was logged"):
            error_exception_mock = cast(Mock, orchestrator.error_logger.exception)
            error_exception_mock.assert_called_once()
            console_exception_mock = cast(Mock, orchestrator.console_logger.exception)
            console_exception_mock.assert_called_once_with("❌ Key rotation failed")
            allure.attach("Error handled gracefully", "Error Handling", allure.attachment_type.TEXT)

    @allure.story("Test Mode")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle test mode with test artists")
    @allure.description("Test that test mode processes only test artists")
    @pytest.mark.asyncio
    async def test_test_mode_execution(self) -> None:
        """Test execution in test mode."""
        with allure.step("Setup orchestrator"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            orchestrator.music_updater = Mock(spec=MusicUpdater)
            orchestrator.music_updater.run_main_pipeline = AsyncMock()
            orchestrator.music_updater.set_dry_run_context = Mock()
            orchestrator.music_updater.database_verifier = Mock()
            orchestrator.music_updater.database_verifier.should_auto_verify = AsyncMock(return_value=False)

            args = self.create_mock_args(test_mode=True)

        with allure.step("Execute in test mode"), patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)

        with allure.step("Verify test mode execution"):
            orchestrator.music_updater.set_dry_run_context.assert_called_once_with(
                "test",
                {"Test Artist 1", "Test Artist 2"},
            )
            cast(MagicMock, orchestrator.console_logger.info).assert_any_call("--- Running in Test Mode ---")
            orchestrator.music_updater.run_main_pipeline.assert_called_once()
            pipeline_kwargs = orchestrator.music_updater.run_main_pipeline.call_args.kwargs
            assert pipeline_kwargs.get("force", False) is False

            allure.attach("Test mode executed with test artists", "Test Mode", allure.attachment_type.TEXT)

    @allure.story("Configuration")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should apply test artists from config in normal mode")
    @allure.description("Test that test artists from config are applied even without test mode")
    @pytest.mark.asyncio
    async def test_test_artists_from_config(self) -> None:
        """Test that test_artists from config are applied in normal mode."""
        with allure.step("Setup orchestrator with test artists"):
            deps = self.create_mock_deps()
            orchestrator = Orchestrator(deps)
            orchestrator.music_updater = Mock(spec=MusicUpdater)
            orchestrator.music_updater.run_main_pipeline = AsyncMock()
            orchestrator.music_updater.set_dry_run_context = Mock()
            orchestrator.music_updater.database_verifier = Mock()
            orchestrator.music_updater.database_verifier.should_auto_verify = AsyncMock(return_value=False)

            # Normal mode but with test_artists in config
            args = self.create_mock_args()

        with allure.step("Execute command"), patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)

        with allure.step("Verify test artists were applied"):
            orchestrator.music_updater.set_dry_run_context.assert_called_once_with(
                "normal",
                {"Test Artist 1", "Test Artist 2"},
            )
            cast(MagicMock, orchestrator.console_logger.info).assert_any_call(
                "Using test_artists from config in normal mode: %s",
                ["Test Artist 1", "Test Artist 2"],
            )

            allure.attach("Test artists applied from config", "Config Application", allure.attachment_type.TEXT)
