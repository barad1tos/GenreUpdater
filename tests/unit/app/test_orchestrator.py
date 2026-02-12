"""Enhanced Orchestrator tests with Allure reporting."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch
import pytest
from app.music_updater import MusicUpdater
from app.orchestrator import Orchestrator
from tests.factories import create_test_app_config

_TEST_PASSWORD = "test-password"  # noqa: S105 - test-only credential placeholder


class TestOrchestratorAllure:
    """Enhanced tests for Orchestrator with Allure reporting."""

    @staticmethod
    def create_mock_deps() -> Mock:
        """Create a mock DependencyContainer."""
        deps = Mock()
        deps.config = {"development": {"test_artists": ["Test Artist 1", "Test Artist 2"]}}
        deps.app_config = create_test_app_config(
            development={"test_artists": ["Test Artist 1", "Test Artist 2"]},
        )
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

    def test_orchestrator_initialization(self) -> None:
        """Test Orchestrator initialization."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        assert orchestrator.deps is deps
        assert isinstance(orchestrator.music_updater, MusicUpdater)
        assert orchestrator.config == deps.app_config
        assert orchestrator.console_logger == deps.console_logger
        assert orchestrator.error_logger == deps.error_logger

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
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        result = orchestrator._requires_music_app(command)
        assert result == requires_music

    @pytest.mark.asyncio
    async def test_music_app_not_running(self) -> None:
        """Test handling when Music app is not running."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        args = self.create_mock_args(command="clean_artist", artist="Test Artist")

        with patch("app.orchestrator.is_music_app_running", return_value=False):
            await orchestrator.run_command(args)
        console_error_mock = cast(Mock, orchestrator.console_logger.error)
        console_error_mock.assert_called_once_with(
            "Music.app is not running - cannot execute '%s' command. Please start Music.app before running this script.",
            "clean_artist",
        )

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
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.run_main_pipeline = AsyncMock()
        orchestrator.music_updater.set_dry_run_context = Mock()
        orchestrator.music_updater.database_verifier = Mock()
        orchestrator.music_updater.database_verifier.should_auto_verify = AsyncMock(return_value=False)
        orchestrator.music_updater.deps = Mock()
        orchestrator.music_updater.deps.pending_verification_service = Mock()
        orchestrator.music_updater.deps.pending_verification_service.should_auto_verify = AsyncMock(return_value=False)

        args = self.create_mock_args(dry_run=dry_run, test_mode=test_mode)

        with patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)
        orchestrator.music_updater.set_dry_run_context.assert_called_once()
        set_context_mock = orchestrator.music_updater.set_dry_run_context
        mode_arg, artists_arg = set_context_mock.call_args.args
        assert mode_arg == expected_mode
        assert artists_arg == {"Test Artist 1", "Test Artist 2"}

    @pytest.mark.asyncio
    async def test_clean_artist_command(self) -> None:
        """Test clean artist command routing."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.run_clean_artist = AsyncMock()

        args = self.create_mock_args(command="clean_artist", artist="Test Artist", force=True)

        with patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)
        orchestrator.music_updater.run_clean_artist.assert_called_once()
        run_clean_artist_mock = orchestrator.music_updater.run_clean_artist
        clean_kwargs = run_clean_artist_mock.call_args.kwargs
        # run_clean_artist should only receive the artist kwarg and no force flag
        assert clean_kwargs == {"artist": "Test Artist"}

    @pytest.mark.asyncio
    async def test_update_years_command(self) -> None:
        """Test update years command routing."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.run_update_years = AsyncMock()

        args = self.create_mock_args(command="update_years", artist="Test Artist", force=True)

        with patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)
        orchestrator.music_updater.run_update_years.assert_called_once()
        update_kwargs = orchestrator.music_updater.run_update_years.call_args.kwargs
        assert update_kwargs["artist"] == "Test Artist"
        assert update_kwargs["force"] is True

    @pytest.mark.asyncio
    async def test_revert_years_command(self) -> None:
        """Test revert years command routing."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.run_revert_years = AsyncMock()

        args = self.create_mock_args(command="revert_years", artist="Test Artist", album="Test Album", backup_csv="backup.csv")

        with patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)
        orchestrator.music_updater.run_revert_years.assert_called_once()
        revert_kwargs = orchestrator.music_updater.run_revert_years.call_args.kwargs
        assert revert_kwargs["artist"] == "Test Artist"
        assert revert_kwargs["album"] == "Test Album"
        assert revert_kwargs["backup_csv"] == "backup.csv"

    @pytest.mark.asyncio
    async def test_verify_database_command(self) -> None:
        """Test verify database command routing."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.run_verify_database = AsyncMock()

        args = self.create_mock_args(command="verify_database", force=True)

        with patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)
        orchestrator.music_updater.run_verify_database.assert_called_once()
        verify_kwargs = orchestrator.music_updater.run_verify_database.call_args.kwargs
        assert verify_kwargs["force"] is True

    @pytest.mark.asyncio
    async def test_main_pipeline_default(self) -> None:
        """Test main pipeline execution when no command specified."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.run_main_pipeline = AsyncMock()
        orchestrator.music_updater.set_dry_run_context = Mock()
        orchestrator.music_updater.database_verifier = Mock()
        orchestrator.music_updater.database_verifier.should_auto_verify = AsyncMock(return_value=False)
        orchestrator.music_updater.deps = Mock()
        orchestrator.music_updater.deps.pending_verification_service = Mock()
        orchestrator.music_updater.deps.pending_verification_service.should_auto_verify = AsyncMock(return_value=False)

        args = self.create_mock_args()

        with patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)
        orchestrator.music_updater.run_main_pipeline.assert_called_once()
        pipeline_kwargs = orchestrator.music_updater.run_main_pipeline.call_args.kwargs
        assert pipeline_kwargs.get("force", False) is False

    @pytest.mark.asyncio
    async def test_batch_command(self) -> None:
        """Test batch processing command."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)

        args = self.create_mock_args(
            command="batch",
            file="batch.txt",
            operation="full",
            force=True,
        )

        with (
            patch("app.orchestrator.is_music_app_running", return_value=True),
            patch("app.orchestrator.BatchProcessor") as mock_batch_processor_class,
        ):
            mock_batch_processor = Mock()
            mock_batch_processor.process_from_file = AsyncMock()
            mock_batch_processor_class.return_value = mock_batch_processor

            await orchestrator.run_command(args)
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

    def test_rotate_keys_command(self) -> None:
        """Test rotate keys command."""
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
            patch("app.orchestrator.SecureConfig", return_value=mock_secure_config),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", MagicMock()),
            patch(
                "yaml.safe_load",
                return_value={
                    "api_authentication": {
                        "discogs_token": "encrypted_token",
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
        mock_secure_config.rotate_key.assert_called_once_with(_TEST_PASSWORD)
        console_info_mock = cast(Mock, orchestrator.console_logger.info)
        console_info_mock.assert_any_call("✅ Encryption key rotation completed (placeholder mode)")

    def test_rotate_keys_error_handling(self) -> None:
        """Test error handling in key rotation."""
        deps = self.create_mock_deps()
        deps.config_path = Path("/test/config.yaml")
        orchestrator = Orchestrator(deps)

        args = self.create_mock_args(command="rotate_keys")

        with (
            patch("app.orchestrator.SecureConfig") as mock_secure_config_class,
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", side_effect=OSError("File error")),
        ):
            mock_secure_config = Mock()
            mock_secure_config_class.return_value = mock_secure_config

            orchestrator._run_rotate_encryption_keys(args)
        error_exception_mock = cast(Mock, orchestrator.error_logger.exception)
        error_exception_mock.assert_called_once()
        console_exception_mock = cast(Mock, orchestrator.console_logger.exception)
        console_exception_mock.assert_called_once_with("❌ Key rotation failed")

    @pytest.mark.asyncio
    async def test_test_mode_execution(self) -> None:
        """Test execution in test mode."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.run_main_pipeline = AsyncMock()
        orchestrator.music_updater.set_dry_run_context = Mock()
        orchestrator.music_updater.database_verifier = Mock()
        orchestrator.music_updater.database_verifier.should_auto_verify = AsyncMock(return_value=False)
        orchestrator.music_updater.deps = Mock()
        orchestrator.music_updater.deps.pending_verification_service = Mock()
        orchestrator.music_updater.deps.pending_verification_service.should_auto_verify = AsyncMock(return_value=False)

        args = self.create_mock_args(test_mode=True)

        with patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)
        orchestrator.music_updater.set_dry_run_context.assert_called_once_with(
            "test",
            {"Test Artist 1", "Test Artist 2"},
        )
        cast(MagicMock, orchestrator.console_logger.info).assert_any_call("--- Running in Test Mode ---")
        orchestrator.music_updater.run_main_pipeline.assert_called_once()
        pipeline_kwargs = orchestrator.music_updater.run_main_pipeline.call_args.kwargs
        assert pipeline_kwargs.get("force", False) is False

    @pytest.mark.asyncio
    async def test_test_artists_from_config(self) -> None:
        """Test that test_artists from config are applied in normal mode."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.run_main_pipeline = AsyncMock()
        orchestrator.music_updater.set_dry_run_context = Mock()
        orchestrator.music_updater.database_verifier = Mock()
        orchestrator.music_updater.database_verifier.should_auto_verify = AsyncMock(return_value=False)
        orchestrator.music_updater.deps = Mock()
        orchestrator.music_updater.deps.pending_verification_service = Mock()
        orchestrator.music_updater.deps.pending_verification_service.should_auto_verify = AsyncMock(return_value=False)

        # Normal mode but with test_artists in config
        args = self.create_mock_args()

        with patch("app.orchestrator.is_music_app_running", return_value=True):
            await orchestrator.run_command(args)
        orchestrator.music_updater.set_dry_run_context.assert_called_once_with(
            "normal",
            {"Test Artist 1", "Test Artist 2"},
        )
        cast(MagicMock, orchestrator.console_logger.info).assert_any_call(
            "Using test_artists from config in normal mode: %s",
            ["Test Artist 1", "Test Artist 2"],
        )


class TestMaybeAutoVerifyPending:
    """Tests for _maybe_auto_verify_pending method."""

    @staticmethod
    def create_mock_deps() -> Mock:
        """Create a mock DependencyContainer."""
        deps = Mock()
        deps.config = {"development": {"test_artists": ["Test Artist 1", "Test Artist 2"]}}
        deps.app_config = create_test_app_config(
            development={"test_artists": ["Test Artist 1", "Test Artist 2"]},
        )
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
        deps.pending_verification_service = Mock()
        return deps

    @pytest.mark.asyncio
    async def test_runs_verify_when_needed(self) -> None:
        """Should run verify_pending when should_auto_verify returns True."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.deps = Mock()
        orchestrator.music_updater.deps.pending_verification_service = Mock()
        orchestrator.music_updater.deps.pending_verification_service.should_auto_verify = AsyncMock(return_value=True)
        orchestrator.music_updater.run_verify_pending = AsyncMock()

        await orchestrator._maybe_auto_verify_pending()

        orchestrator.music_updater.run_verify_pending.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_verify_when_not_needed(self) -> None:
        """Should skip verify_pending when should_auto_verify returns False."""
        deps = self.create_mock_deps()
        orchestrator = Orchestrator(deps)
        orchestrator.music_updater = Mock(spec=MusicUpdater)
        orchestrator.music_updater.deps = Mock()
        orchestrator.music_updater.deps.pending_verification_service = Mock()
        orchestrator.music_updater.deps.pending_verification_service.should_auto_verify = AsyncMock(return_value=False)
        orchestrator.music_updater.run_verify_pending = AsyncMock()

        await orchestrator._maybe_auto_verify_pending()

        orchestrator.music_updater.run_verify_pending.assert_not_called()
