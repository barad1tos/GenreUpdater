"""Dependency Injection Container Module.

Manages the lifecycle and dependency relationships between application services.
Centralizes service initialization, configuration, and proper shutdown procedures.
Provides access to configured service instances including loggers, analytics,
API clients, and core application components. Handles circular imports and
asynchronous resource management.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypeVar

import yaml

from src.shared.core.config import load_config
from src.shared.core.dry_run import DryRunAppleScriptClient
from src.shared.monitoring.analytics import Analytics, LoggerContainer

from .api.orchestrator import ExternalApiOrchestrator, create_external_api_orchestrator
from .applescript_client import AppleScriptClient
from .cache.cache_orchestrator import CacheOrchestrator
from .cache.library_snapshot_service import LibrarySnapshotService
from .pending_verification import PendingVerificationService

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable

    from src.shared.core.logger import SafeQueueListener
    from src.shared.data.protocols import AppleScriptClientProtocol

T = TypeVar("T")


class InitializableService(Protocol):
    """Protocol for services that can be initialized.

    Note: Services may have different initialize signatures,
    handled dynamically by _initialize_service method.
    """

    def initialize(self, *args: Any, **kwargs: Any) -> Awaitable[None]:
        """Initialize the service."""
        ...


class DependencyContainer:
    """Dependency injection container for the application.

    This class manages the lifecycle and dependencies of all services in the application.
    It follows the singleton pattern to ensure only one instance exists.
    """

    # Class-level instance tracking
    _instance: ClassVar[DependencyContainer | None] = None
    _initialized: ClassVar[bool] = False
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    def __init__(
        self,
        config_path: str,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics_logger: logging.Logger,
        year_updates_logger: logging.Logger,
        db_verify_logger: logging.Logger,
        *,
        logging_listener: SafeQueueListener | None = None,
        dry_run: bool = False,
    ) -> None:
        """Initialize the dependency container.

        Args:
            config_path: Path to the configuration file
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics_logger: Logger for analytics events
            year_updates_logger: Logger for year update operations
            db_verify_logger: Logger for database verification operations
            logging_listener: Optional queue listener for logging
            dry_run: Whether to run in dry-run mode (no changes made)


        """
        # Initialize logger properties first
        self._console_logger = console_logger
        self._error_logger = error_logger
        self._analytics_logger = analytics_logger
        self._year_updates_logger = year_updates_logger
        self._db_verify_logger = db_verify_logger
        self._listener = logging_listener

        # Initialize service references
        self._config_path = config_path
        self._config: dict[str, Any] = {}
        self._analytics: Analytics | None = None
        self._ap_client: AppleScriptClientProtocol | None = None
        self._cache_service: CacheOrchestrator | None = None
        self._library_snapshot_service: LibrarySnapshotService | None = None
        self._pending_verification_service: PendingVerificationService | None = None
        self._api_orchestrator: ExternalApiOrchestrator | None = None
        self._dry_run = dry_run

    @property
    def dry_run(self) -> bool:
        """Get the dry run status."""
        return self._dry_run

    @property
    def config(self) -> dict[str, Any]:
        """Get the application configuration."""
        return self._config

    @property
    def config_path(self) -> Path:
        """Get the resolved configuration file path."""
        return Path(self._config_path)

    @property
    def analytics(self) -> Analytics:
        """Get the analytics service."""
        if self._analytics is None:
            msg = "Analytics service not initialized"
            raise RuntimeError(msg)
        return self._analytics

    @property
    def ap_client(self) -> AppleScriptClientProtocol:
        """Get the AppleScript client."""
        if self._ap_client is None:
            msg = "AppleScript client not initialized"
            raise RuntimeError(msg)
        return self._ap_client

    @property
    def cache_service(self) -> CacheOrchestrator:
        """Get the cache service."""
        if self._cache_service is None:
            msg = "Cache service not initialized"
            raise RuntimeError(msg)
        return self._cache_service

    @property
    def library_snapshot_service(self) -> LibrarySnapshotService:
        """Get the library snapshot service."""
        if self._library_snapshot_service is None:
            msg = "Library snapshot service not initialized"
            raise RuntimeError(msg)
        return self._library_snapshot_service

    @property
    def pending_verification_service(self) -> PendingVerificationService:
        """Get the pending verification service."""
        if self._pending_verification_service is None:
            msg = "Pending verification service not initialized"
            raise RuntimeError(msg)
        return self._pending_verification_service

    @property
    def external_api_service(self) -> ExternalApiOrchestrator:
        """Get the external API orchestrator."""
        if self._api_orchestrator is None:
            msg = "External API orchestrator not initialized"
            raise RuntimeError(msg)
        return self._api_orchestrator

    @property
    def console_logger(self) -> logging.Logger:
        """Get the console logger."""
        return self._console_logger

    @property
    def error_logger(self) -> logging.Logger:
        """Get the error logger."""
        return self._error_logger

    @property
    def analytics_logger(self) -> logging.Logger:
        """Get the analytics logger."""
        return self._analytics_logger

    async def _initialize_service(
        self,
        service: InitializableService | None,
        service_name: str,
        *,
        force: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize a service instance.

        This helper detects whether the target object exposes an ``initializing``
        coroutine or synchronous method, times its execution, injects optional
        keyword arguments, and logs any failure with useful context.

        Args:
            service (object): The service instance to initialize.
            service_name (str): Human-readable name for logging purposes.
            force (bool, optional): If *True* and the target ``initializes``
                signature accepts a ``force`` flag, it will be passed through.
                Defaults to ``False``.
            **kwargs: Additional keyword arguments forwarded to the underlying
                ``initializing`` implementation.

        """
        initialize_method = getattr(service, "initialize", None)
        if not callable(initialize_method):
            self._error_logger.warning(
                f" {service_name} instance has no initialize method",
            )
            return

        self._console_logger.debug(f" Initializing {service_name}...")
        start = time.monotonic()

        try:
            # Prepare kwargs for initialize
            init_kwargs = dict(kwargs)
            if force:
                sig = inspect.signature(initialize_method)
                if "force" in sig.parameters:
                    init_kwargs["force"] = True

            # Call the initialize method and await if it's a coroutine
            result = initialize_method(**init_kwargs)
            if asyncio.iscoroutine(result):
                await result

            elapsed = time.monotonic() - start
            self._console_logger.debug(f" {service_name} initialized in {elapsed:.2f}s")

        except Exception as e:
            elapsed = time.monotonic() - start
            self._error_logger.exception(
                f" Failed to initialize {service_name} after {elapsed:.2f}s: {e}"
            )
            raise

    def _initialize_apple_script_client(self, dry_run: bool) -> None:
        """Initialize the appropriate AppleScript client based on the dry-run flag."""
        if self._analytics is None:
            msg = "Analytics must be initialized before AppleScript client"
            raise RuntimeError(msg)

        # Type assertion after None check to help type checker
        analytics = self._analytics
        if dry_run:
            # Create the real client first
            real_client = AppleScriptClient(
                self._config,
                analytics,
                self._console_logger,
                self._error_logger,
            )
            # Then wrap it with DryRunAppleScriptClient
            self._ap_client = DryRunAppleScriptClient(
                real_client,
                self._config,
                self._console_logger,
                self._error_logger,
            )
            self._console_logger.info("Dry run enabled - using DryRunAppleScriptClient")
        else:
            self._ap_client = AppleScriptClient(
                self._config,
                analytics,
                self._console_logger,
                self._error_logger,
            )

        self._log_apple_scripts_dir(self._ap_client, dry_run)

    async def initialize(self) -> None:
        """Initialize all services with async setup requirements."""
        self._console_logger.info("Starting async initialization of services...")

        # Load configuration first
        if not self._config:
            self._config = self._load_config()

        # Construct missing service instances
        if self._analytics is None:
            loggers = LoggerContainer(
                self._console_logger,
                self._error_logger,
                self._analytics_logger,
            )
            self._analytics = Analytics(
                self._config,
                loggers,
            )
        if self._cache_service is None:
            self._cache_service = CacheOrchestrator(self._config, self._console_logger)
        if self._library_snapshot_service is None:
            self._library_snapshot_service = LibrarySnapshotService(self._config, self._console_logger)
        if self._pending_verification_service is None:
            self._pending_verification_service = PendingVerificationService(
                self._config, self._console_logger, self._error_logger
            )
        if self._api_orchestrator is None:
            self._api_orchestrator = create_external_api_orchestrator(
                self._config,
                self._console_logger,
                self._error_logger,
                self._analytics,
                self._cache_service,
                self._pending_verification_service,
            )

        # Ensure the AppleScript client is instantiated
        if self._ap_client is None:
            self._initialize_apple_script_client(self._dry_run)

        # Initialize services in the correct order
        services: list[tuple[InitializableService | None, str]] = [
            (self._library_snapshot_service, "Library Snapshot Service"),
            (self._cache_service, "Cache Service"),
            (self._pending_verification_service, "Pending Verification Service"),
            (self._api_orchestrator, "API Orchestrator"),
            (self._ap_client, "AppleScript Client"),
        ]

        for service, name in services:
            if service is not None:
                await self._initialize_service(service, name)

        # Ensure the AppleScript client is initialized
        if self._ap_client is None:
            msg = "AppleScript client must be initialized"
            raise RuntimeError(msg)

        self._console_logger.info(" All services initialized successfully")

    # MusicUpdater factory removed - now created by orchestrator

    def get_analytics(self) -> Analytics:
        """Get the Analytics instance."""
        if self._analytics is None:
            msg = "Analytics not initialized"
            raise RuntimeError(msg)
        return self._analytics

    def get_error_logger(self) -> logging.Logger:
        """Get the error logger."""
        return self._error_logger

    def get_console_logger(self) -> logging.Logger:
        """Get the console logger."""
        return self._console_logger

    async def close(self) -> None:
        """Async cleanup of resources and services."""
        self._console_logger.debug("Closing DependencyContainer...")

        # Save cache before closing
        if self._cache_service is not None:
            try:
                await self._cache_service.save_all_to_disk()
                self._console_logger.debug("Cache saved successfully")
            except Exception as e:
                self._console_logger.warning(f"Failed to save cache: {e}")
            finally:
                try:
                    await self._cache_service.shutdown()
                except Exception as e:
                    self._console_logger.warning(f"Failed to shutdown cache services: {e}")

        # Close API Orchestrator's aiohttp session properly
        if self._api_orchestrator is not None:
            if not hasattr(self._api_orchestrator, "close"):
                self._console_logger.error(
                    "API Orchestrator does not have a 'close' method. "
                    "Possible interface change or initialization error."
                )
            else:
                try:
                    await self._api_orchestrator.close()
                    self._console_logger.debug("API Orchestrator closed successfully")
                except (OSError, RuntimeError, asyncio.CancelledError) as e:
                    self._console_logger.warning(f"Failed to close API Orchestrator: {e}")

        self._console_logger.debug("DependencyContainer closed.")

    def shutdown(self) -> None:
        """Clean up non-async resources and stop services."""
        self._console_logger.debug("Shutting down DependencyContainer...")

        if self._listener is not None:
            self._console_logger.debug("Stopping logging listener...")
            self._listener.stop()
            self._listener = None
        self._console_logger.debug("DependencyContainer shutdown complete.")

    async def _async_run(self, coro: Awaitable[T]) -> T:
        """Run a coroutine in the current event loop.

        Args:
            coro: The coroutine to execute

        Returns:
            The result of the coroutine

        Raises:
            RuntimeError: If coroutine execution fails

        """
        try:
            return await coro
        except asyncio.CancelledError:
            self._error_logger.warning("Coroutine was cancelled")
            raise
        except Exception as e:
            self._error_logger.exception(f"Error in async operation: {e}")
            msg = f"Failed to execute coroutine: {e}"
            raise RuntimeError(msg) from e

    def _handle_initialization_errors(
        self,
        error: Exception,
        service_name: str,
        start_time: float,
        error_type: str,
    ) -> None:
        """Handle and log service initialization errors.

        Args:
            error: The exception that was raised
            service_name: Name of the service that failed to initialize
            start_time: When the initialization started (from time.monotonic())
            error_type: Type/category of the error

        """
        elapsed = time.monotonic() - start_time
        self._error_logger.error(
            f" {error_type} initializing {service_name} after {elapsed:.2f}s: {error}",
            exc_info=not isinstance(error, KeyboardInterrupt | SystemExit),
        )

    def _log_apple_scripts_dir(
        self,
        client: AppleScriptClientProtocol | None,
        is_dry_run: bool = False,
    ) -> None:
        """Log the AppleScripts directory being used.

        Args:
            client: The AppleScript client (real or dry-run)
            is_dry_run: Whether this is a dry run

        """
        if client is None:
            self._console_logger.warning(
                "AppleScript client is not initialized - cannot log scripts directory",
            )
            return

        # Get the scripts directory safely
        scripts_dir = getattr(client, "apple_scripts_dir", None)
        if not scripts_dir:
            self._console_logger.warning(
                "AppleScripts directory is not configured in the client",
            )
            return

        # Convert to string if it's a Path object
        scripts_dir_str = str(scripts_dir)

        # Validate directory exists and handle permission issues gracefully
        try:
            scripts_path = Path(scripts_dir_str)
            if not scripts_path.is_dir():
                self._console_logger.error(
                    f"AppleScripts directory does not exist: {scripts_dir_str}",
                )
                return
        except PermissionError as exc:
            self._console_logger.exception(
                f"Permission denied when accessing AppleScripts directory: {scripts_dir_str}. Error: {exc}",
            )
            return
        except Exception as exc:
            self._console_logger.exception(
                f"Unexpected error when checking AppleScripts directory: {scripts_dir_str}. Error: {exc}",
            )
            return

        # Log the directory being used
        run_type = "DRY RUN - " if is_dry_run else ""
        self._console_logger.info(
            f"{run_type}Using AppleScripts directory: {scripts_dir_str}",
        )

    def _load_config(self) -> dict[str, Any]:
        """Load and validate application configuration.

        Returns:
            dict: The loaded configuration

        Raises:
            FileNotFoundError: If the config file doesn't exist
            yaml.YAMLError: If there's an error parsing the YAML
            RuntimeError: For any other errors during loading

        """
        try:
            self._console_logger.info(f"Loading configuration from {self._config_path}")
            config = load_config(self._config_path)
            self._console_logger.info("Configuration loaded successfully")
            return config
        except FileNotFoundError as e:
            self._error_logger.exception(f"Configuration file not found: {self._config_path}")
            msg = f"Configuration file not found: {self._config_path}"
            raise FileNotFoundError(msg) from e
        except yaml.YAMLError as e:
            self._error_logger.exception(f"Invalid YAML in config file {self._config_path}: {e}")
            raise
