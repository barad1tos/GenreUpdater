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

from core.core_config import load_config, validate_api_auth
from core.dry_run import DryRunAppleScriptClient
from core.logger import LogFormat, shorten_path
from core.models.album_type import configure_patterns as configure_album_patterns
from core.retry_handler import DatabaseRetryHandler, RetryPolicy
from metrics.analytics import Analytics, LoggerContainer

from .api.orchestrator import ExternalApiOrchestrator, create_external_api_orchestrator
from .apple import AppleScriptClient
from .apple.swift_bridge import SwiftBridge
from .cache.orchestrator import CacheOrchestrator
from .cache.snapshot import LibrarySnapshotService
from .pending_verification import PendingVerificationService

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable

    from core.logger import SafeQueueListener
    from core.models.protocols import AppleScriptClientProtocol

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
        db_verify_logger: logging.Logger,
        *,
        logging_listener: SafeQueueListener | None = None,
        dry_run: bool = False,
        skip_api_validation: bool = False,
    ) -> None:
        """Initialize the dependency container.

        Args:
            config_path: Path to the configuration file
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics_logger: Logger for analytics events
            db_verify_logger: Logger for database verification operations
            logging_listener: Optional queue listener for logging
            dry_run: Whether to run in dry-run mode (no changes made)
            skip_api_validation: Whether to skip API auth validation (for non-API commands)

        """
        # Initialize logger properties first
        self._console_logger = console_logger
        self._error_logger = error_logger
        self._analytics_logger = analytics_logger
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
        self._retry_handler: DatabaseRetryHandler | None = None
        self._dry_run = dry_run
        self._skip_api_validation = skip_api_validation

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
    def retry_handler(self) -> DatabaseRetryHandler:
        """Get the retry handler."""
        if self._retry_handler is None:
            msg = "Retry handler not initialized"
            raise RuntimeError(msg)
        return self._retry_handler

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

    @property
    def db_verify_logger(self) -> logging.Logger:
        """Get the database verification logger."""
        return self._db_verify_logger

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
                " %s instance has no initialize method",
                LogFormat.entity(service_name),
            )
            return

        self._console_logger.debug(" Initializing %s...", LogFormat.entity(service_name))
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
            self._console_logger.debug(" %s initialized in %.2fs", LogFormat.entity(service_name), elapsed)

        except Exception as e:
            elapsed = time.monotonic() - start
            self._error_logger.exception(" Failed to initialize %s after %.2fs: %s", LogFormat.entity(service_name), elapsed, e)
            raise

    def _initialize_apple_script_client(self, dry_run: bool) -> None:
        """Initialize the appropriate Music.app client based on configuration.

        Selection priority:
        1. If dry_run=True → DryRunAppleScriptClient (always, for safety)
        2. If swift_helper.enabled=True → SwiftBridge (~60x faster)
        3. Otherwise → AppleScriptClient (traditional, stable)
        """
        if self._analytics is None:
            msg = "Analytics must be initialized before AppleScript client"
            raise RuntimeError(msg)

        # Type assertion after None check to help type checker
        analytics = self._analytics

        # Check Swift helper configuration
        swift_config = self._config.get("swift_helper", {})
        swift_enabled = swift_config.get("enabled", False)

        if dry_run:
            # Dry-run always uses AppleScript client for safety
            real_client = AppleScriptClient(
                self._config,
                analytics,
                self._console_logger,
                self._error_logger,
                retry_handler=self._retry_handler,
            )
            self._ap_client = DryRunAppleScriptClient(
                real_client,
                self._config,
                self._console_logger,
                self._error_logger,
            )
            self._console_logger.info(
                "Dry run enabled - using %s",
                LogFormat.entity("DryRunAppleScriptClient"),
            )
        elif swift_enabled:
            # Use SwiftBridge for ~60x performance improvement
            self._ap_client = SwiftBridge(
                self._config,
                analytics,
                self._console_logger,
                self._error_logger,
            )
            self._console_logger.info(
                "Swift helper enabled - using %s (~60x faster)",
                LogFormat.entity("SwiftBridge"),
            )
        else:
            # Traditional AppleScript client
            self._ap_client = AppleScriptClient(
                self._config,
                analytics,
                self._console_logger,
                self._error_logger,
                retry_handler=self._retry_handler,
            )

        self._log_apple_scripts_dir(self._ap_client, dry_run)

    async def initialize(self) -> None:
        """Initialize all services with async setup requirements."""
        self._console_logger.info("Starting async initialization of services...")

        # Load configuration first
        if not self._config:
            self._config = self._load_config()

        # Configure album type detection patterns from config
        configure_album_patterns(self._config)

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
            self._pending_verification_service = PendingVerificationService(self._config, self._console_logger, self._error_logger)
        if self._api_orchestrator is None:
            self._api_orchestrator = create_external_api_orchestrator(
                self._config,
                self._console_logger,
                self._error_logger,
                self._analytics,
                self._cache_service,
                self._pending_verification_service,
            )

        # Initialize retry handler from config
        if self._retry_handler is None:
            retry_config = self._config.get("applescript_retry", {})
            retry_policy = RetryPolicy(
                max_retries=retry_config.get("max_retries", 3),
                base_delay_seconds=retry_config.get("base_delay_seconds", 1.0),
                max_delay_seconds=retry_config.get("max_delay_seconds", 10.0),
                jitter_range=retry_config.get("jitter_range", 0.2),
                operation_timeout_seconds=retry_config.get("operation_timeout_seconds", 60.0),
            )
            self._retry_handler = DatabaseRetryHandler(
                logger=self._console_logger,
                default_policy=retry_policy,
            )
            self._console_logger.debug(
                "Retry handler initialized with policy: max_retries=%d, base_delay=%.1fs",
                retry_policy.max_retries,
                retry_policy.base_delay_seconds,
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
        """Close all services in the correct order.

        Order: API first (to flush pending tasks), then cache (to persist data).
        This prevents API orchestrator from writing to a closed cache during shutdown.
        """
        self._console_logger.debug("Closing %s...", LogFormat.entity("DependencyContainer"))

        # 1. FIRST: Close API orchestrator (flushes pending tasks)
        if self._api_orchestrator is not None:
            if not hasattr(self._api_orchestrator, "close"):
                self._console_logger.error("API Orchestrator does not have a 'close' method. Possible interface change or initialization error.")
            else:
                try:
                    await self._api_orchestrator.close()
                    self._console_logger.debug("%s closed successfully", LogFormat.entity("ExternalApiOrchestrator"))
                except (OSError, RuntimeError, asyncio.CancelledError) as e:
                    self._console_logger.warning("Failed to close API Orchestrator: %s", e)

        # 2. THEN: Save and shutdown cache
        if self._cache_service is not None:
            try:
                await self._cache_service.save_all_to_disk()
                self._console_logger.debug("Cache saved successfully")
            except Exception as e:
                self._console_logger.warning("Failed to save cache: %s", e)
            finally:
                try:
                    await self._cache_service.shutdown()
                except Exception as e:
                    self._console_logger.warning("Failed to shutdown cache services: %s", e)

        # 3. FINALLY: Shutdown SwiftBridge daemon if active
        if self._ap_client is not None and isinstance(self._ap_client, SwiftBridge):
            try:
                await self._ap_client.shutdown()
                self._console_logger.debug(
                    "%s daemon terminated",
                    LogFormat.entity("SwiftBridge"),
                )
            except Exception as e:
                self._console_logger.warning("Failed to shutdown SwiftBridge: %s", e)

        self._console_logger.debug("%s closed.", LogFormat.entity("DependencyContainer"))

    def shutdown(self) -> None:
        """Clean up non-async resources and stop services."""
        self._console_logger.debug("Shutting down %s...", LogFormat.entity("DependencyContainer"))

        if self._listener is not None:
            self._console_logger.debug("Stopping logging listener...")
            self._listener.stop()
            self._listener = None
        self._console_logger.debug("%s shutdown complete.", LogFormat.entity("DependencyContainer"))

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
            " %s initializing %s after %.2fs: %s",
            error_type,
            LogFormat.entity(service_name),
            elapsed,
            error,
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
                short_path = shorten_path(scripts_dir_str, self._config, self._error_logger)
                self._console_logger.error("AppleScripts directory does not exist: %s", short_path)
                return
        except PermissionError as exc:
            short_path = shorten_path(scripts_dir_str, self._config, self._error_logger)
            self._console_logger.exception("Permission denied for AppleScripts directory: %s. Error: %s", short_path, exc)
            return
        except Exception as exc:
            short_path = shorten_path(scripts_dir_str, self._config, self._error_logger)
            self._console_logger.exception("Error checking AppleScripts directory: %s. Error: %s", short_path, exc)
            return

        # Log the directory being used with shortened path
        short_path = shorten_path(scripts_dir_str, self._config, self._error_logger)
        run_type = "DRY RUN - " if is_dry_run else ""
        self._console_logger.info("%sAppleScripts: %s", run_type, short_path)

    def _load_config(self) -> dict[str, Any]:
        """Load and validate application configuration.

        Returns:
            dict: The loaded configuration

        Raises:
            FileNotFoundError: If the config file doesn't exist
            yaml.YAMLError: If there's an error parsing the YAML
            ValueError: If API authentication configuration is incomplete
            RuntimeError: For any other errors during loading

        """
        try:
            config = load_config(self._config_path)
            self._console_logger.info("Configuration: [cyan]%s[/cyan]", Path(self._config_path).name)

            # Validate API auth configuration (fail-fast for API-dependent commands)
            if not self._skip_api_validation:
                year_config = config.get("year_retrieval", {})
                api_auth = year_config.get("api_auth", {})
                validate_api_auth(api_auth)

            return config
        except FileNotFoundError as e:
            short_path = Path(self._config_path).name
            self._error_logger.exception("Configuration file not found: %s", short_path)
            msg = f"Configuration file not found: {self._config_path}"
            raise FileNotFoundError(msg) from e
        except yaml.YAMLError as e:
            short_path = Path(self._config_path).name
            self._error_logger.exception("Invalid YAML in config file %s: %s", short_path, e)
            raise
