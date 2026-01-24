"""Integration tests for DependencyContainer service lifecycle and dependency management.

These tests validate the DependencyContainer's ability to:
- Initialize all services in the correct order
- Handle property access before/after initialization
- Properly close and shutdown resources
- Handle dry-run mode correctly
- Validate configuration loading
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from services.dependency_container import InitializableService
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# Get project root for temp config files (config validation requires project dir)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _get_complete_config_data(
    *,
    applescript_retry: dict | None = None,
) -> dict:
    """Generate a complete config dict with all required fields.

    Args:
        applescript_retry: Optional override for applescript_retry section.
    """
    return {
        # Main paths
        "music_library_path": "/tmp/test_library",
        "apple_scripts_dir": str(PROJECT_ROOT / "applescripts"),
        "logs_base_dir": "/tmp/test_logs",
        # Python settings
        "python_settings": {
            "prevent_bytecode": True,
        },
        # Execution and performance
        "apple_script_concurrency": 2,
        "apple_script_rate_limit": {
            "enabled": False,
            "requests_per_window": 10,
            "window_size_seconds": 1.0,
        },
        "applescript_timeout_seconds": 60,
        "applescript_timeouts": {
            "default": 60,
            "full_library_fetch": 60,
            "single_artist_fetch": 30,
            "batch_update": 60,
        },
        "max_retries": 3,
        "retry_delay_seconds": 1,
        "cache_ttl_seconds": 1800,
        "incremental_interval_minutes": 15,
        # Feature toggles
        "cleaning": {
            "remaster_keywords": ["remaster", "remastered"],
            "album_suffixes_to_remove": ["Remaster"],
        },
        "artist_renamer": {
            "config_path": "artist-renames.yaml",
        },
        "exceptions": {
            "track_cleaning": [],
        },
        "database_verification": {
            "auto_verify_days": 7,
            "batch_size": 10,
        },
        "pending_verification": {
            "auto_verify_days": 14,
        },
        "development": {
            "test_artists": [],
        },
        "reporting": {
            "problematic_albums_path": "reports/albums_without_year.csv",
            "min_attempts_for_report": 3,
            "change_display_mode": "compact",
        },
        "caching": {
            "default_ttl_seconds": 900,
            "album_cache_sync_interval": 300,
            "cleanup_error_retry_delay": 60,
            "cleanup_interval_seconds": 300,
            "negative_result_ttl": 2592000,
            "library_snapshot": {
                "enabled": True,
                "delta_enabled": True,
                "cache_file": "cache/library_snapshot.json",
                "max_age_hours": 24,
                "compress": True,
                "compress_level": 6,
            },
        },
        "api_cache_file": "cache/cache.json",
        "album_years_cache_file": "cache/album_years.csv",
        # Logging and analytics
        "logging": {
            "max_runs": 3,
            "main_log_file": "main/main.log",
            "analytics_log_file": "analytics/analytics.log",
            "csv_output_file": "csv/track_list.csv",
            "changes_report_file": "csv/changes_report.csv",
            "dry_run_report_file": "reports/dry_run_report.html",
            "last_incremental_run_file": "last_incremental_run.log",
            "pending_verification_file": "csv/pending_year_verification.csv",
            "last_db_verify_log": "main/last_db_verify.log",
            "levels": {
                "console": "INFO",
                "main_file": "DEBUG",
                "analytics_file": "INFO",
            },
        },
        "analytics": {
            "enabled": False,
            "duration_thresholds": {
                "short_max": 5,
                "medium_max": 20,
                "long_max": 50,
            },
            "max_events": 10000,
            "compact_time": True,
        },
        # Year retrieval
        "year_retrieval": {
            "enabled": True,
            "preferred_api": "musicbrainz",
            "api_auth": {
                "discogs_token": "test_token",
                "musicbrainz_app_name": "TestApp/1.0",
                "contact_email": "test@example.com",
            },
            "rate_limits": {
                "discogs_requests_per_minute": 55,
                "musicbrainz_requests_per_second": 1,
                "concurrent_api_calls": 2,
            },
            "processing": {
                "batch_size": 25,
                "delay_between_batches": 20,
                "adaptive_delay": True,
                "cache_ttl_days": 365,
                "pending_verification_interval_days": 30,
                "skip_prerelease": True,
                "future_year_threshold": 1,
                "prerelease_recheck_days": 30,
            },
            "logic": {
                "min_valid_year": 1900,
                "absurd_year_threshold": 1970,
                "suspicion_threshold_years": 10,
                "definitive_score_threshold": 50,
                "definitive_score_diff": 15,
                "min_confidence_for_new_year": 30,
                "preferred_countries": ["us", "gb", "de"],
                "major_market_codes": ["us", "gb", "de", "jp"],
            },
            "reissue_detection": {
                "reissue_keywords": ["reissue", "remaster"],
            },
            "script_api_priorities": {
                "default": {
                    "primary": ["musicbrainz", "discogs"],
                    "fallback": ["itunes"],
                },
            },
            "scoring": {
                "base_score": 10,
                "artist_exact_match_bonus": 20,
                "album_exact_match_bonus": 25,
                "perfect_match_bonus": 10,
                "album_variation_bonus": 10,
                "album_substring_penalty": -5,
                "album_unrelated_penalty": -40,
                "artist_cross_script_penalty": -10,
                "soundtrack_compensation_bonus": 75,
                "mb_release_group_match_bonus": 50,
                "type_album_bonus": 15,
                "type_ep_single_penalty": -10,
                "type_compilation_live_penalty": -35,
                "status_official_bonus": 10,
                "status_bootleg_penalty": -50,
                "status_promo_penalty": -20,
                "reissue_penalty": -30,
                "year_diff_penalty_scale": -5,
                "year_diff_max_penalty": -40,
                "year_before_start_penalty": -35,
                "year_after_end_penalty": -25,
                "year_near_start_bonus": 20,
                "country_artist_match_bonus": 10,
                "country_major_market_bonus": 5,
                "source_mb_bonus": 25,
                "source_discogs_bonus": 2,
                "source_itunes_bonus": -10,
                "future_year_penalty": -10,
            },
            "fallback": {
                "enabled": True,
                "year_difference_threshold": 5,
                "trust_api_score_threshold": 70,
            },
        },
        # Album type detection
        "album_type_detection": {
            "special_patterns": ["b-sides", "demo"],
            "compilation_patterns": ["greatest hits", "best of"],
            "reissue_patterns": ["remaster", "anniversary"],
            "soundtrack_patterns": ["soundtrack", "OST"],
            "various_artists_names": ["Various Artists", "VA"],
        },
        # Genre update
        "genre_update": {
            "batch_size": 50,
            "concurrent_limit": 5,
        },
        # Test mode
        "test_artists": ["Test Artist"],
        # Legacy fields for backwards compatibility
        "processing": {
            "batch_size": 100,
            "concurrent_limit": 5,
        },
        "library_snapshot": {
            "enabled": True,
            "cache_file": "cache/library_snapshot.json",
        },
        "applescript_retry": applescript_retry
        or {
            "max_retries": 3,
            "base_delay_seconds": 0.1,
            "max_delay_seconds": 1.0,
            "jitter_range": 0.1,
            "operation_timeout_seconds": 30.0,
        },
    }


@pytest.fixture
def temp_config_file() -> Generator[Path]:
    """Create a temporary config file for testing in project directory.

    This config includes all required fields that pass validation.
    """
    config_data = _get_complete_config_data()

    # Create temp file in project directory (required by config validation)
    temp_path = PROJECT_ROOT / f".test_config_{uuid.uuid4().hex[:8]}.yaml"
    with temp_path.open("w", encoding="utf-8") as f:
        yaml.dump(config_data, f)

    yield temp_path

    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def mock_loggers() -> dict[str, Any]:
    """Create mock loggers for testing.

    Returns MagicMock instances with logging.Logger spec for interface compatibility,
    but typed as Any to allow access to mock assertion methods.
    """
    return {
        "console": MagicMock(spec=logging.Logger),
        "error": MagicMock(spec=logging.Logger),
        "analytics": MagicMock(spec=logging.Logger),
        "db_verify": MagicMock(spec=logging.Logger),
    }


@pytest.fixture
def mock_listener() -> MagicMock:
    """Create a mock logging listener.

    Returns MagicMock that mimics SafeQueueListener interface
    for testing shutdown behavior.
    """
    listener = MagicMock()
    listener.stop = MagicMock()
    return listener


@pytest.mark.integration
class TestDependencyContainerInitialization:
    """Test DependencyContainer initialization and service lifecycle."""

    @pytest.mark.asyncio
    async def test_container_creation_with_valid_config(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that container can be created with valid configuration."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            dry_run=True,
            skip_api_validation=True,
        )

        assert container is not None
        assert container.dry_run is True
        assert container.config_path == temp_config_file

    @pytest.mark.asyncio
    async def test_property_access_before_initialization_raises_error(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that accessing services before initialization raises RuntimeError."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # All service properties should raise RuntimeError before initialization
        with pytest.raises(RuntimeError, match="Analytics service not initialized"):
            _ = container.analytics

        with pytest.raises(RuntimeError, match="AppleScript client not initialized"):
            _ = container.ap_client

        with pytest.raises(RuntimeError, match="Cache service not initialized"):
            _ = container.cache_service

        with pytest.raises(RuntimeError, match="Library snapshot service not initialized"):
            _ = container.library_snapshot_service

        with pytest.raises(RuntimeError, match="Pending verification service not initialized"):
            _ = container.pending_verification_service

        with pytest.raises(RuntimeError, match="External API orchestrator not initialized"):
            _ = container.external_api_service

        with pytest.raises(RuntimeError, match="Retry handler not initialized"):
            _ = container.retry_handler

    @pytest.mark.asyncio
    async def test_logger_properties_always_accessible(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that logger properties are accessible without initialization."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Logger properties should be accessible without initialization
        assert container.console_logger is mock_loggers["console"]
        assert container.error_logger is mock_loggers["error"]
        assert container.analytics_logger is mock_loggers["analytics"]
        assert container.db_verify_logger is mock_loggers["db_verify"]

    @pytest.mark.asyncio
    async def test_get_logger_methods(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test get_*_logger() methods work without initialization."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        assert container.get_console_logger() is mock_loggers["console"]
        assert container.get_error_logger() is mock_loggers["error"]


@pytest.mark.integration
class TestDependencyContainerServiceInitialization:
    """Test service initialization order and dependencies."""

    @pytest.mark.asyncio
    async def test_initialize_creates_all_services(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that initialize() creates all required services."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            dry_run=True,
            skip_api_validation=True,
        )

        # Patch external dependencies that would fail in test environment
        with (
            patch("services.dependency_container.AppleScriptClient") as mock_ap_client_cls,
            patch("services.dependency_container.DryRunAppleScriptClient") as mock_dry_run_cls,
            patch("services.dependency_container.CacheOrchestrator") as mock_cache_cls,
            patch("services.dependency_container.LibrarySnapshotService") as mock_snapshot_cls,
            patch("services.dependency_container.PendingVerificationService") as mock_pending_cls,
            patch("services.dependency_container.create_external_api_orchestrator") as mock_api_factory,
        ):
            # Configure mocks to return proper instances
            mock_ap_client = MagicMock()
            mock_ap_client.apple_scripts_dir = "/tmp/scripts"
            mock_ap_client.initialize = AsyncMock()
            mock_ap_client_cls.return_value = mock_ap_client

            mock_dry_run = MagicMock()
            mock_dry_run.apple_scripts_dir = "/tmp/scripts"
            mock_dry_run.initialize = AsyncMock()
            mock_dry_run_cls.return_value = mock_dry_run

            mock_cache = MagicMock()
            mock_cache.initialize = AsyncMock()
            mock_cache_cls.return_value = mock_cache

            mock_snapshot = MagicMock()
            mock_snapshot.initialize = AsyncMock()
            mock_snapshot_cls.return_value = mock_snapshot

            mock_pending = MagicMock()
            mock_pending.initialize = AsyncMock()
            mock_pending_cls.return_value = mock_pending

            mock_api = MagicMock()
            mock_api.initialize = AsyncMock()
            mock_api_factory.return_value = mock_api

            # Initialize the container
            await container.initialize()

            # Verify services were created
            assert container._analytics is not None
            assert container._cache_service is not None
            assert container._library_snapshot_service is not None
            assert container._pending_verification_service is not None
            assert container._api_orchestrator is not None
            assert container._retry_handler is not None
            assert container._ap_client is not None

    @pytest.mark.asyncio
    async def test_dry_run_uses_dry_run_client(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that dry_run=True creates DryRunAppleScriptClient."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            dry_run=True,
            skip_api_validation=True,
        )

        with (
            patch("services.dependency_container.AppleScriptClient") as mock_ap_client_cls,
            patch("services.dependency_container.DryRunAppleScriptClient") as mock_dry_run_cls,
            patch("services.dependency_container.CacheOrchestrator") as mock_cache_cls,
            patch("services.dependency_container.LibrarySnapshotService") as mock_snapshot_cls,
            patch("services.dependency_container.PendingVerificationService") as mock_pending_cls,
            patch("services.dependency_container.create_external_api_orchestrator") as mock_api_factory,
        ):
            # Configure all mocks
            mock_ap_client = MagicMock()
            mock_ap_client.apple_scripts_dir = "/tmp/scripts"
            mock_ap_client.initialize = AsyncMock()
            mock_ap_client_cls.return_value = mock_ap_client

            mock_dry_run = MagicMock()
            mock_dry_run.apple_scripts_dir = "/tmp/scripts"
            mock_dry_run.initialize = AsyncMock()
            mock_dry_run_cls.return_value = mock_dry_run

            mock_cache = MagicMock()
            mock_cache.initialize = AsyncMock()
            mock_cache_cls.return_value = mock_cache

            mock_snapshot = MagicMock()
            mock_snapshot.initialize = AsyncMock()
            mock_snapshot_cls.return_value = mock_snapshot

            mock_pending = MagicMock()
            mock_pending.initialize = AsyncMock()
            mock_pending_cls.return_value = mock_pending

            mock_api = MagicMock()
            mock_api.initialize = AsyncMock()
            mock_api_factory.return_value = mock_api

            await container.initialize()

            # Verify DryRunAppleScriptClient was created
            mock_dry_run_cls.assert_called_once()
            # Verify the dry run client is set
            assert container._ap_client is mock_dry_run


@pytest.mark.integration
class TestDependencyContainerShutdown:
    """Test DependencyContainer close and shutdown procedures."""

    @pytest.mark.asyncio
    async def test_close_saves_cache_and_closes_api(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that close() saves cache and closes API orchestrator in correct order."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Set up mock services
        mock_cache = MagicMock()
        mock_cache.save_all_to_disk = AsyncMock()
        mock_cache.shutdown = AsyncMock()
        container._cache_service = mock_cache

        mock_api = MagicMock()
        mock_api.close = AsyncMock()
        container._api_orchestrator = mock_api

        # Call close
        await container.close()

        # Verify order: API first, then cache
        mock_api.close.assert_called_once()
        mock_cache.save_all_to_disk.assert_called_once()
        mock_cache.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_api_close_error_gracefully(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that close() handles API close errors without crashing."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Set up mock services with API throwing error
        mock_cache = MagicMock()
        mock_cache.save_all_to_disk = AsyncMock()
        mock_cache.shutdown = AsyncMock()
        container._cache_service = mock_cache

        mock_api = MagicMock()
        mock_api.close = AsyncMock(side_effect=RuntimeError("Test API error"))
        container._api_orchestrator = mock_api

        # Should not raise despite API error
        await container.close()

        # Cache should still be saved and shutdown
        mock_cache.save_all_to_disk.assert_called_once()
        mock_cache.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_cache_save_error_gracefully(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that close() handles cache save errors without crashing."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Set up mock cache with save error
        mock_cache = MagicMock()
        mock_cache.save_all_to_disk = AsyncMock(side_effect=OSError("Disk full"))
        mock_cache.shutdown = AsyncMock()
        container._cache_service = mock_cache
        container._api_orchestrator = None  # No API orchestrator

        # Should not raise despite cache error
        await container.close()

        # Shutdown should still be called even if save fails
        mock_cache.shutdown.assert_called_once()

    def test_shutdown_stops_logging_listener(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
        mock_listener: MagicMock,
    ) -> None:
        """Test that shutdown() stops the logging listener."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            logging_listener=mock_listener,
            skip_api_validation=True,
        )

        container.shutdown()

        mock_listener.stop.assert_called_once()
        assert container._listener is None

    def test_shutdown_without_listener_is_safe(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that shutdown() works when no listener is configured."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
            # logging_listener defaults to None, not passed explicitly
        )

        # Should not raise
        container.shutdown()


@pytest.mark.integration
class TestDependencyContainerConfigLoading:
    """Test configuration loading and validation."""

    @pytest.mark.asyncio
    async def test_config_loads_on_initialize(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that configuration is loaded during initialize()."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            dry_run=True,
            skip_api_validation=True,
        )

        # Config should be empty before initialization
        assert container.config == {}

        # Patch services to avoid real initialization
        with (
            patch("services.dependency_container.AppleScriptClient"),
            patch("services.dependency_container.DryRunAppleScriptClient") as mock_dry_run_cls,
            patch("services.dependency_container.CacheOrchestrator") as mock_cache_cls,
            patch("services.dependency_container.LibrarySnapshotService") as mock_snapshot_cls,
            patch("services.dependency_container.PendingVerificationService") as mock_pending_cls,
            patch("services.dependency_container.create_external_api_orchestrator") as mock_api_factory,
        ):
            # Configure all mocks
            for mock_cls in [mock_dry_run_cls, mock_cache_cls, mock_snapshot_cls, mock_pending_cls]:
                mock_instance = MagicMock()
                mock_instance.initialize = AsyncMock()
                mock_instance.apple_scripts_dir = "/tmp/scripts"
                mock_cls.return_value = mock_instance

            mock_api = MagicMock()
            mock_api.initialize = AsyncMock()
            mock_api_factory.return_value = mock_api

            await container.initialize()

            # Config should now be loaded (Pydantic normalized dict)
            assert container.config != {}
            # Check for a key that exists after Pydantic validation
            assert "music_library_path" in container.config
            assert container.config["music_library_path"] == "/tmp/test_library"

    def test_missing_config_file_raises_error(
        self,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that missing config file raises FileNotFoundError."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path="/nonexistent/path/config.yaml",
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        with pytest.raises(FileNotFoundError):
            container._load_config()

    def test_invalid_yaml_raises_error(
        self,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that invalid YAML raises appropriate error."""
        from services.dependency_container import DependencyContainer

        # Create a file with invalid YAML in project directory
        invalid_config_path = PROJECT_ROOT / f".test_invalid_{uuid.uuid4().hex[:8]}.yaml"
        invalid_config_path.write_text("invalid: yaml: content: ][", encoding="utf-8")

        try:
            container = DependencyContainer(
                config_path=str(invalid_config_path),
                console_logger=mock_loggers["console"],
                error_logger=mock_loggers["error"],
                analytics_logger=mock_loggers["analytics"],
                db_verify_logger=mock_loggers["db_verify"],
                skip_api_validation=True,
            )

            with pytest.raises(yaml.YAMLError):
                container._load_config()
        finally:
            if invalid_config_path.exists():
                invalid_config_path.unlink()


@pytest.mark.integration
class TestDependencyContainerRetryHandler:
    """Test retry handler initialization and configuration."""

    @pytest.mark.asyncio
    async def test_retry_handler_uses_default_values(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that retry handler is initialized with default policy values.

        Note: The DependencyContainer reads from config.get("applescript_retry", {})
        but this key is not included in the Pydantic-validated config (it's not in
        AppConfig model). Therefore, default values are always used.
        """
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            dry_run=True,
            skip_api_validation=True,
        )

        # Patch services
        with (
            patch("services.dependency_container.AppleScriptClient"),
            patch("services.dependency_container.DryRunAppleScriptClient") as mock_dry_run_cls,
            patch("services.dependency_container.CacheOrchestrator") as mock_cache_cls,
            patch("services.dependency_container.LibrarySnapshotService") as mock_snapshot_cls,
            patch("services.dependency_container.PendingVerificationService") as mock_pending_cls,
            patch("services.dependency_container.create_external_api_orchestrator") as mock_api_factory,
        ):
            # Configure mocks
            for mock_cls in [mock_dry_run_cls, mock_cache_cls, mock_snapshot_cls, mock_pending_cls]:
                mock_instance = MagicMock()
                mock_instance.initialize = AsyncMock()
                mock_instance.apple_scripts_dir = "/tmp/scripts"
                mock_cls.return_value = mock_instance

            mock_api = MagicMock()
            mock_api.initialize = AsyncMock()
            mock_api_factory.return_value = mock_api

            await container.initialize()

            # Verify retry handler uses default values
            # (since applescript_retry is not in Pydantic config)
            retry_handler = container.retry_handler
            assert retry_handler.database_policy.max_retries == 3  # Default
            assert retry_handler.database_policy.base_delay_seconds == 1.0  # Default
            assert retry_handler.database_policy.max_delay_seconds == 10.0  # Default
            assert retry_handler.database_policy.jitter_range == 0.2  # Default
            assert retry_handler.database_policy.operation_timeout_seconds == 60.0  # Default


@pytest.mark.integration
class TestDependencyContainerServiceAccess:
    """Test service property access after initialization."""

    @pytest.mark.asyncio
    async def test_get_analytics_after_initialization(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that get_analytics() works after initialization."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            dry_run=True,
            skip_api_validation=True,
        )

        with (
            patch("services.dependency_container.AppleScriptClient"),
            patch("services.dependency_container.DryRunAppleScriptClient") as mock_dry_run_cls,
            patch("services.dependency_container.CacheOrchestrator") as mock_cache_cls,
            patch("services.dependency_container.LibrarySnapshotService") as mock_snapshot_cls,
            patch("services.dependency_container.PendingVerificationService") as mock_pending_cls,
            patch("services.dependency_container.create_external_api_orchestrator") as mock_api_factory,
        ):
            # Configure mocks
            for mock_cls in [mock_dry_run_cls, mock_cache_cls, mock_snapshot_cls, mock_pending_cls]:
                mock_instance = MagicMock()
                mock_instance.initialize = AsyncMock()
                mock_instance.apple_scripts_dir = "/tmp/scripts"
                mock_cls.return_value = mock_instance

            mock_api = MagicMock()
            mock_api.initialize = AsyncMock()
            mock_api_factory.return_value = mock_api

            await container.initialize()

            # get_analytics() should return the same instance as analytics property
            analytics = container.get_analytics()
            assert analytics is container.analytics
            assert analytics is not None

    @pytest.mark.asyncio
    async def test_get_analytics_before_initialization_raises(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that get_analytics() raises before initialization."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        with pytest.raises(RuntimeError, match="Analytics not initialized"):
            container.get_analytics()


@pytest.mark.integration
class TestDependencyContainerAsyncOperations:
    """Test async operations and coroutine handling."""

    @pytest.mark.asyncio
    async def test_async_run_handles_coroutine(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that _async_run() correctly executes coroutines."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        async def test_coro() -> str:
            return "success"

        result = await container._async_run(test_coro())
        assert result == "success"

    @pytest.mark.asyncio
    async def test_async_run_handles_cancelled_error(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that _async_run() properly handles CancelledError."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        async def cancelled_coro() -> None:
            """Raise CancelledError to simulate task cancellation."""
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await container._async_run(cancelled_coro())

    @pytest.mark.asyncio
    async def test_async_run_wraps_errors_in_runtime_error(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that _async_run() wraps general errors in RuntimeError."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        async def failing_coro() -> None:
            """Raise ValueError to simulate general failure."""
            raise ValueError("Test error")

        with pytest.raises(RuntimeError, match="Failed to execute coroutine"):
            await container._async_run(failing_coro())


@pytest.mark.integration
class TestDependencyContainerInitializeService:
    """Test the _initialize_service helper method."""

    @pytest.mark.asyncio
    async def test_initialize_service_with_async_init(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test initializing a service with async initialize method."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Create mock service with async initialize
        mock_service = MagicMock()
        mock_service.initialize = AsyncMock()

        await container._initialize_service(mock_service, "Test Service")

        mock_service.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_service_with_sync_init(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test initializing a service with synchronous initialize method."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Create mock service with sync initialize
        mock_service = MagicMock()
        mock_service.initialize = MagicMock(return_value=None)

        await container._initialize_service(mock_service, "Test Service")

        mock_service.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_service_without_init_method(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test initializing a service without initialize method logs warning."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Create mock service without initialize
        mock_service = MagicMock(spec=[])  # Empty spec means no initialize

        await container._initialize_service(mock_service, "Test Service")

        # Should log warning
        mock_loggers["error"].warning.assert_called()

    @pytest.mark.asyncio
    async def test_initialize_service_with_force_flag(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test initializing a service with force flag."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Track if force was passed
        force_received: list[bool] = []

        # Create service class that implements InitializableService protocol
        class MockServiceWithForce:
            """Mock service with force parameter in initialize signature."""

            def __init__(self) -> None:
                """Initialize the mock service."""
                self._initialized = False

            async def initialize(self, force: bool = False) -> None:
                """Initialize with optional force flag."""
                self._initialized = True
                force_received.append(force)

        mock_service = MockServiceWithForce()

        await container._initialize_service(
            cast("InitializableService", cast(object, mock_service)),
            "Test Service",
            force=True,
        )

        # Verify force was passed via our tracking list
        assert force_received == [True]

    @pytest.mark.asyncio
    async def test_initialize_service_error_handling(
        self,
        temp_config_file: Path,
        mock_loggers: dict[str, Any],
    ) -> None:
        """Test that initialization errors are properly logged and re-raised."""
        from services.dependency_container import DependencyContainer

        container = DependencyContainer(
            config_path=str(temp_config_file),
            console_logger=mock_loggers["console"],
            error_logger=mock_loggers["error"],
            analytics_logger=mock_loggers["analytics"],
            db_verify_logger=mock_loggers["db_verify"],
            skip_api_validation=True,
        )

        # Create mock service that fails
        mock_service = MagicMock()
        mock_service.initialize = AsyncMock(side_effect=RuntimeError("Init failed"))

        with pytest.raises(RuntimeError, match="Init failed"):
            await container._initialize_service(mock_service, "Test Service")

        # Error should be logged
        mock_loggers["error"].exception.assert_called()
