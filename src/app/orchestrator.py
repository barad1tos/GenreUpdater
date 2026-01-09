"""Main orchestrator module for Music Genre Updater.

This module handles the high-level coordination of all operations.
"""

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import yaml

from app.features.batch.batch_processor import BatchProcessor
from app.music_updater import MusicUpdater
from core.models.metadata_utils import is_music_app_running, reset_cleaning_exceptions_log
from stubs.cryptography.secure_config import SecureConfig, SecurityConfigError

if TYPE_CHECKING:
    from services.dependency_container import DependencyContainer


class Orchestrator:
    """Orchestrates the entire music update workflow."""

    COMMANDS_BYPASSING_MUSIC_CHECK: ClassVar[set[str]] = {"rotate_keys", "rotate-keys"}

    def __init__(self, deps: "DependencyContainer") -> None:
        """Initialize the orchestrator with dependencies.

        Args:
            deps: Dependency container with all required services

        """
        self.deps = deps
        self.music_updater = MusicUpdater(deps)
        self.config = deps.config
        self.console_logger = deps.console_logger
        self.error_logger = deps.error_logger

    async def run_command(self, args: argparse.Namespace) -> None:
        """Execute the appropriate command based on arguments.

        Args:
            args: Parsed command-line arguments

        """
        # Reset per-run state
        reset_cleaning_exceptions_log()

        # Handle --fresh flag: clear all caches and snapshots
        if getattr(args, "fresh", False):
            self.console_logger.info("--fresh flag set: clearing all caches and snapshots...")
            await self.deps.cache_service.clear()
            self.deps.library_snapshot_service.clear_snapshot()
            self.console_logger.info("All caches cleared. Will fetch fresh data from Music.app.")

        command = getattr(args, "command", None)

        # Check if Music app is running for commands that depend on it
        if self._requires_music_app(command) and not is_music_app_running(self.error_logger):
            self.console_logger.error("Music app is not running! Please start Music.app before running this script.")
            return

        # Set dry-run context if needed
        if args.dry_run or getattr(args, "test_mode", False):
            test_artists = set(self.config.get("development", {}).get("test_artists", []))
            mode = "test" if getattr(args, "test_mode", False) else "dry_run"
            self.music_updater.set_dry_run_context(mode, test_artists)

        # ALWAYS apply test_artists from config if configured (even in --force mode)
        elif test_artists_config := set(self.config.get("development", {}).get("test_artists", [])):
            self.console_logger.info("Using test_artists from config in normal mode: %s", sorted(test_artists_config))
            self.music_updater.set_dry_run_context("normal", test_artists_config)

        # Route to an appropriate command
        match args.command:
            case "clean_artist" | "clean":
                await self._run_clean_artist(args)
            case "update_years" | "years":
                await self._run_update_years(args)
            case "update_genres" | "genres":
                await self._run_update_genres(args)
            case "revert_years" | "revert":
                await self._run_revert_years(args)
            case "restore_release_years" | "restore":
                await self._run_restore_release_years(args)
            case "verify_database" | "verify-db":
                await self._run_verify_database(args)
            case "verify_pending" | "pending":
                await self._run_verify_pending(args)
            case "batch":
                await self._run_batch(args)
            case "rotate_keys" | "rotate-keys":
                self._run_rotate_encryption_keys(args)
            case _:
                await self._run_main_workflow(args)

    def _requires_music_app(self, command: str | None) -> bool:
        """Determine whether the given command depends on Music.app being available."""
        return command not in self.COMMANDS_BYPASSING_MUSIC_CHECK

    async def _run_clean_artist(self, args: argparse.Namespace) -> None:
        """Run the clean artist command."""
        await self.music_updater.run_clean_artist(artist=args.artist)

    async def _run_update_years(self, args: argparse.Namespace) -> None:
        """Run the update years command."""
        await self.music_updater.run_update_years(
            artist=getattr(args, "artist", None),
            force=args.force,
            fresh=getattr(args, "fresh", False),
        )

    async def _run_update_genres(self, args: argparse.Namespace) -> None:
        """Run the update genres command."""
        await self.music_updater.run_update_genres(
            artist=getattr(args, "artist", None),
            force=args.force,
        )

    async def _run_revert_years(self, args: argparse.Namespace) -> None:
        """Run the revert years command."""
        await self.music_updater.run_revert_years(
            artist=args.artist,
            album=getattr(args, "album", None),
            backup_csv=getattr(args, "backup_csv", None),
        )

    async def _run_restore_release_years(self, args: argparse.Namespace) -> None:
        """Run the restore release years command."""
        artist = getattr(args, "artist", None)
        album = getattr(args, "album", None)

        # Validate: --album requires --artist
        if album and not artist:
            self.error_logger.error("--album requires --artist to be specified")
            return

        await self.music_updater.run_restore_release_years(
            artist=artist,
            album=album,
            threshold=getattr(args, "threshold", 5),
        )

    async def _run_verify_database(self, args: argparse.Namespace) -> None:
        """Run the verify database command."""
        await self.music_updater.run_verify_database(force=args.force)

    async def _run_verify_pending(self, args: argparse.Namespace) -> None:
        """Run the verification pending command."""
        await self.music_updater.run_verify_pending(_force=args.force)

    async def _run_main_workflow(self, args: argparse.Namespace) -> None:
        """Run the main update workflow when no specific command is given."""
        # Auto-verify database if threshold days passed (before main workflow)
        await self._maybe_auto_verify()

        # Auto-verify pending albums if threshold days passed
        await self._maybe_auto_verify_pending()

        # Check if in test mode
        if getattr(args, "test_mode", False):
            await self._run_test_mode(args)
        else:
            # Run the main pipeline
            await self.music_updater.run_main_pipeline(force=args.force, fresh=args.fresh)

    async def _maybe_auto_verify(self) -> None:
        """Run automatic database verification if threshold days have passed."""
        should_verify = await self.music_updater.database_verifier.should_auto_verify()
        self.console_logger.info(
            "Auto-verify check: %s",
            "running verification" if should_verify else "not needed yet",
        )
        if should_verify:
            await self.music_updater.run_verify_database()

    async def _maybe_auto_verify_pending(self) -> None:
        """Run automatic pending verification if threshold days have passed."""
        should_verify = await self.music_updater.deps.pending_verification_service.should_auto_verify()
        self.console_logger.info(
            "Auto-verify pending check: %s",
            "running verification" if should_verify else "not needed yet",
        )
        if should_verify:
            await self.music_updater.run_verify_pending()

    async def _run_test_mode(self, args: argparse.Namespace) -> None:
        """Run in test mode with a limited artist set."""
        self.console_logger.info("--- Running in Test Mode ---")
        test_artists = self.config.get("development", {}).get("test_artists", [])
        self.console_logger.info(
            "Processing tracks only for test artists: %s",
            test_artists,
        )

        # Run pipeline for test artists only
        await self.music_updater.run_main_pipeline(force=args.force, fresh=args.fresh)

    async def _run_batch(self, args: argparse.Namespace) -> None:
        """Run batch processing from a file."""
        batch_processor = BatchProcessor(self.music_updater, self.console_logger, self.error_logger)

        await batch_processor.process_from_file(
            file_path=args.file,
            operation=getattr(args, "operation", "full"),
            force=args.force,
        )

    def _decrypt_existing_tokens(self, secure_config: SecureConfig, api_auth: dict[str, Any]) -> dict[str, str]:
        """Decrypt existing tokens from configuration.

        Args:
            secure_config: Configured SecureConfig instance
            api_auth: API authentication configuration section

        Returns:
            Dictionary of decrypted tokens

        """
        sensitive_keys: list[str] = ["discogs_token"]
        current_tokens: dict[str, str] = {}

        for key in sensitive_keys:
            token_value: str = api_auth.get(key, "")
            if token_value and secure_config.is_token_encrypted(token_value):
                try:
                    current_tokens[key] = secure_config.decrypt_token(token_value, key)
                    self.console_logger.debug("Decrypted current %s", key)
                except SecurityConfigError as e:
                    self.error_logger.warning("Could not decrypt %s: %s", key, str(e))
                    current_tokens[key] = token_value
            else:
                current_tokens[key] = token_value

        return current_tokens

    def _create_backups(self, args: argparse.Namespace, secure_config: SecureConfig, config_path: Path) -> None:
        """Create backups of encryption key and configuration files.

        Args:
            args: Command-line arguments containing backup preferences
            secure_config: SecureConfig instance for key file access
            config_path: Path to a configuration file

        """
        # Create backup of old key if requested
        if not args.no_backup:
            key_file_path: Path = Path(secure_config.key_file_path)
            if key_file_path.exists():
                self._create_backup_file(
                    key_file_path,
                    ".key.backup",
                    "Created backup of old key: %s",
                    preserve_existing=True,  # Critical: preserve previous key backups
                )
        self._create_backup_file(config_path, ".yaml.backup", "Created backup of config: %s")

    def _create_backup_file(self, source_path: Path, suffix: str, success_message: str, preserve_existing: bool = False) -> None:
        """Create a backup of a file with the specified suffix.

        Args:
            source_path: Path to the source file to backup
            suffix: File suffix for the backup (e.g., '.backup', '.yaml.backup')
            success_message: Message to log on successful backup creation
            preserve_existing: If True and backup exists, create timestamped backup instead of overwriting

        """
        # Append suffix to full filename (preserves multi-dot names like config.prod.yaml)
        backup_path: Path = source_path.parent / f"{source_path.name}{suffix}"

        # If preserving existing backups and file exists, use timestamped name
        if preserve_existing and backup_path.exists():
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            backup_path = source_path.parent / f"{source_path.name}_{timestamp}{suffix}"
            self.console_logger.warning("Previous backup exists, creating timestamped backup: %s", backup_path)

        try:
            shutil.copy2(source_path, backup_path)
            self.console_logger.info(success_message, backup_path)
        except OSError as exc:
            self.console_logger.exception("Failed to backup %s to %s: %s", source_path, backup_path, exc)
            raise

    def _re_encrypt_tokens(self, secure_config: "SecureConfig", current_tokens: dict[str, str]) -> dict[str, str]:
        """Re-encrypt tokens with a new encryption key.

        Args:
            secure_config: SecureConfig with new encryption key
            current_tokens: Dictionary of decrypted tokens

        Returns:
            Dictionary of newly encrypted tokens

        """
        self.console_logger.info("Re-encrypting %d tokens with new key...", len(current_tokens))

        re_encrypted_tokens: dict[str, str] = {}
        for key, token_value in current_tokens.items():
            try:
                # Try to encrypt with the new key
                encrypted_token = secure_config.encrypt_token(token_value, key)
                re_encrypted_tokens[key] = encrypted_token
                self.console_logger.debug("Re-encrypted %s", key)
            except SecurityConfigError as e:
                self.error_logger.warning("Could not re-encrypt %s: %s", key, str(e))
                # Keep the original token if encryption fails
                re_encrypted_tokens[key] = token_value

        return re_encrypted_tokens

    def _display_rotation_status(self, secure_config: "SecureConfig", new_password: str | None) -> None:
        """Display the status after key rotation completion.

        Args:
            secure_config: SecureConfig instance to check status
            new_password: New password used (None if auto-generated)

        """
        self.console_logger.info("Key rotation status:")

        # Get status from secure config
        status = secure_config.get_secure_config_status()

        self.console_logger.info("  Key file path: %s", status.get("key_file_path", "Unknown"))
        self.console_logger.info(
            "  Encryption initialized: %s",
            "yes" if status.get("encryption_initialized", False) else "no",
        )
        self.console_logger.info(
            "  Password configured: %s",
            "yes" if status.get("password_configured", False) else "no",
        )

        if new_password:
            self.console_logger.info("  New password: [PROVIDED]")
        else:
            self.console_logger.info("  New password: [AUTO-GENERATED]")

        self.console_logger.warning("Note: Security features are in placeholder mode")

    def _run_rotate_encryption_keys(self, args: argparse.Namespace) -> None:
        """Rotate encryption keys and re-encrypt all tokens.

        Args:
            args: Command-line arguments containing:
                - new_password: Optional new password for key derivation
                - no_backup: Whether to skip creating backup

        """
        self.console_logger.info("Starting encryption key rotation...")

        try:
            # Initialize secure config with logger
            secure_config = SecureConfig(self.error_logger)

            # Get current configuration
            config_path = self.deps.config_path
            if not config_path.exists():
                self.error_logger.error("Configuration file not found: %s", config_path)
                return

            # Load current config
            with config_path.open(encoding="utf-8") as f:
                current_config = yaml.safe_load(f)

            # Get API authentication section
            api_auth = current_config.get("api_authentication", {})
            if not api_auth:
                self.console_logger.info("No API authentication section found in configuration")
                return

            # Step 1: Decrypt existing tokens
            self.console_logger.info("Step 1: Decrypting existing tokens...")
            current_tokens = self._decrypt_existing_tokens(secure_config, api_auth)

            if not current_tokens:
                self.console_logger.info("No encrypted tokens found to rotate")
                return

            # Step 2: Create backups
            self.console_logger.info("Step 2: Creating backups...")
            self._create_backups(args, secure_config, config_path)

            # Step 3: Generate a new key (placeholder)
            self.console_logger.info("Step 3: Generating new encryption key...")
            new_password = getattr(args, "new_password", None)
            try:
                secure_config.rotate_key(new_password)
                self.console_logger.info("New encryption key generated")
            except SecurityConfigError as e:
                self.error_logger.warning("Key rotation failed: %s", str(e))
                self.console_logger.info("Continuing with placeholder implementation...")

            # Step 4: Re-encrypt tokens
            self.console_logger.info("Step 4: Re-encrypting tokens...")
            if new_encrypted_tokens := self._re_encrypt_tokens(secure_config, current_tokens):
                self.console_logger.info("Step 5: Updating configuration...")
                self.console_logger.info(
                    "Would update %d tokens in configuration",
                    len(new_encrypted_tokens),
                )
            # Step 6: Display status
            self.console_logger.info("Step 6: Displaying rotation status...")
            self._display_rotation_status(secure_config, new_password)

            self.console_logger.info("✅ Encryption key rotation completed (placeholder mode)")

        except (OSError, SecurityConfigError, yaml.YAMLError) as e:
            self.error_logger.exception("Error during key rotation: %s", e)
            self.console_logger.exception("❌ Key rotation failed")
