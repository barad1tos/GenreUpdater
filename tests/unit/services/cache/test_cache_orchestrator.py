"""Comprehensive unit tests for CacheOrchestrator."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.models.track_models import TrackDict
from services.cache.orchestrator import CacheOrchestrator

if TYPE_CHECKING:
    from core.models.protocols import CacheableValue


class TestCacheOrchestrator:
    """Comprehensive tests for CacheOrchestrator."""

    @staticmethod
    def create_orchestrator(config: dict[str, Any] | None = None) -> CacheOrchestrator:
        """Create a CacheOrchestrator instance for testing."""
        temp_dir = tempfile.mkdtemp()
        default_config = {
            "caching": {
                "default_ttl_seconds": 5,
                "cleanup_interval_seconds": 1,
                "api_result_cache_path": str(Path(temp_dir) / "api_results.json"),
                "album_cache_sync_interval": 1,
            },
            "api_cache_file": str(Path(temp_dir) / "cache.json"),
            "album_years_cache_file": str(Path(temp_dir) / "album_years.csv"),
            "generic_cache_file": str(Path(temp_dir) / "generic_cache.json"),
            "logging": {"base_dir": temp_dir},
            "log_directory": temp_dir,
        }
        test_config = {**default_config, **(config or {})}
        mock_logger = MagicMock(spec=logging.Logger)
        return CacheOrchestrator(test_config, mock_logger)

    # =========================== INITIALIZATION TESTS ===========================

    @pytest.mark.asyncio
    async def test_initialization_creates_services(self) -> None:
        """Test that initialization creates all required services."""
        orchestrator = self.create_orchestrator()

        assert orchestrator.album_service is not None
        assert orchestrator.api_service is not None
        assert orchestrator.generic_service is not None
        assert len(orchestrator._services) == 3

    @pytest.mark.asyncio
    async def test_initialize_calls_all_services(self) -> None:
        """Test that initialize() calls initialize on all services."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.album_service, "initialize", new_callable=AsyncMock) as mock_album,
            patch.object(orchestrator.api_service, "initialize", new_callable=AsyncMock) as mock_api,
            patch.object(orchestrator.generic_service, "initialize", new_callable=AsyncMock) as mock_generic,
        ):
            await orchestrator.initialize()

            mock_album.assert_called_once()
            mock_api.assert_called_once()
            mock_generic.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_handles_service_failure(self) -> None:
        """Test that initialization raises RuntimeError when a service fails."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.album_service, "initialize", new_callable=AsyncMock, side_effect=Exception("Album init failed")),
            patch.object(orchestrator.api_service, "initialize", new_callable=AsyncMock),
            patch.object(orchestrator.generic_service, "initialize", new_callable=AsyncMock),
            pytest.raises(RuntimeError, match="Cache service initialization failed"),
        ):
            await orchestrator.initialize()

    # =========================== ALBUM CACHE TESTS ===========================

    @pytest.mark.asyncio
    async def test_get_album_year_delegates(self) -> None:
        """Test that get_album_year delegates to album service."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.album_service, "get_album_year", new_callable=AsyncMock, return_value="1999") as mock_get:
            result = await orchestrator.get_album_year("Artist", "Album")

            assert result == "1999"
            mock_get.assert_called_once_with("Artist", "Album")

    @pytest.mark.asyncio
    async def test_store_album_year_delegates(self) -> None:
        """Test that store_album_year delegates to album service."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.album_service, "store_album_year", new_callable=AsyncMock) as mock_store:
            await orchestrator.store_album_year("Artist", "Album", "1999", 85)

            mock_store.assert_called_once_with("Artist", "Album", "1999", 85)

    # =========================== API CACHE TESTS ===========================

    @pytest.mark.asyncio
    async def test_get_api_result_delegates(self) -> None:
        """Test that get_api_result delegates to api service."""
        orchestrator = self.create_orchestrator()
        mock_result = MagicMock()
        mock_result.api_response = {"year": "2020"}

        with patch.object(orchestrator.api_service, "get_cached_result", new_callable=AsyncMock, return_value=mock_result) as mock_get:
            result = await orchestrator.get_api_result("Artist", "Album", "musicbrainz")

            assert result == {"year": "2020"}
            mock_get.assert_called_once_with("Artist", "Album", "musicbrainz")

    @pytest.mark.asyncio
    async def test_get_api_result_returns_none(self) -> None:
        """Test that get_api_result returns None when not cached."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.api_service, "get_cached_result", new_callable=AsyncMock, return_value=None):
            result = await orchestrator.get_api_result("Artist", "Album", "discogs")

            assert result is None

    @pytest.mark.asyncio
    async def test_store_api_result_delegates(self) -> None:
        """Test that store_api_result delegates to api service."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.api_service, "set_cached_result", new_callable=AsyncMock) as mock_set:
            await orchestrator.store_api_result("Artist", "Album", "discogs", {"data": "value"})

            mock_set.assert_called_once()

    # =========================== GENERIC CACHE TESTS ===========================

    @pytest.mark.asyncio
    async def test_get_returns_cached_value(self) -> None:
        """Test that get() returns cached value."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.generic_service, "get", return_value="cached_value") as mock_get:
            result = orchestrator.get("test_key")

            assert result == "cached_value"
            mock_get.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_set_delegates_to_generic_service(self) -> None:
        """Test that set() delegates to generic service."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.generic_service, "set") as mock_set:
            orchestrator.set("test_key", "test_value", ttl=60)

            mock_set.assert_called_once_with("test_key", "test_value", 60)

    @pytest.mark.asyncio
    async def test_get_async_computes_on_miss(self) -> None:
        """Test that get_async computes value when not cached."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.generic_service, "get", return_value=None), patch.object(orchestrator.generic_service, "set") as mock_set:

            async def _compute_value() -> CacheableValue:
                """Compute the cached value."""
                return "computed"

            def compute_func() -> asyncio.Future[CacheableValue]:
                """Create compute future."""
                return asyncio.create_task(_compute_value())

            result = await orchestrator.get_async("missing_key", compute_func)

            assert result == "computed"
            mock_set.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_async_returns_cached_without_computing(self) -> None:
        """Test that get_async returns cached value without calling compute."""
        orchestrator = self.create_orchestrator()

        compute_called = False

        with patch.object(orchestrator.generic_service, "get", return_value="cached"):

            async def _compute_value() -> CacheableValue:
                """Compute the cached value."""
                nonlocal compute_called
                compute_called = True
                return "computed"

            def compute_func() -> asyncio.Future[CacheableValue]:
                """Create compute future."""
                return asyncio.create_task(_compute_value())

            result = await orchestrator.get_async("existing_key", compute_func)

            assert result == "cached"
            assert not compute_called

    @pytest.mark.asyncio
    async def test_set_async_delegates(self) -> None:
        """Test that set_async delegates to generic service."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.generic_service, "set") as mock_set:
            await orchestrator.set_async("key", "value", ttl=30)

            mock_set.assert_called_once_with("key", "value", 30)

    # =========================== INVALIDATION TESTS ===========================

    @pytest.mark.asyncio
    async def test_invalidate_for_track(self) -> None:
        """Test that invalidate_for_track invalidates all related caches."""
        orchestrator = self.create_orchestrator()
        orchestrator.api_service.event_manager = MagicMock()

        with (
            patch.object(orchestrator.generic_service, "invalidate") as mock_invalidate,
            patch.object(orchestrator.album_service, "invalidate_album", new_callable=AsyncMock) as mock_album,
            patch.object(orchestrator.api_service, "invalidate_for_album", new_callable=AsyncMock) as mock_api,
        ):
            track = TrackDict(
                id="123",
                name="Test Track",
                artist="Test Artist",
                album="Test Album",
                genre="Rock",
            )

            await orchestrator.invalidate_for_track(track)

            # Should invalidate generic caches
            mock_invalidate.assert_any_call("tracks_all")
            # Should invalidate album and API caches
            mock_album.assert_called_once_with("Test Artist", "Test Album")
            mock_api.assert_called_once_with("Test Artist", "Test Album")

    @pytest.mark.asyncio
    async def test_invalidate_single_key(self) -> None:
        """Test that invalidate() invalidates a single key."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.generic_service, "invalidate") as mock_invalidate:
            orchestrator.invalidate("test_key")

            mock_invalidate.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_invalidate_all(self) -> None:
        """Test that invalidate_all clears all caches."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.album_service, "invalidate_all", new_callable=AsyncMock) as mock_album,
            patch.object(orchestrator.api_service, "invalidate_all", new_callable=AsyncMock) as mock_api,
            patch.object(orchestrator.generic_service, "invalidate_all") as mock_generic,
        ):
            await orchestrator.invalidate_all()

            mock_album.assert_called_once()
            mock_api.assert_called_once()
            mock_generic.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_all_to_disk(self) -> None:
        """Test that save_all_to_disk saves all services."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.album_service, "save_to_disk", new_callable=AsyncMock) as mock_album,
            patch.object(orchestrator.api_service, "save_to_disk", new_callable=AsyncMock) as mock_api,
            patch.object(orchestrator.generic_service, "save_to_disk", new_callable=AsyncMock) as mock_generic,
        ):
            await orchestrator.save_all_to_disk()

            mock_album.assert_called_once()
            mock_api.assert_called_once()
            mock_generic.assert_called_once()

    # =========================== STATISTICS TESTS ===========================

    @pytest.mark.asyncio
    async def test_get_comprehensive_stats(self) -> None:
        """Test that get_comprehensive_stats aggregates stats from all services."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.album_service, "get_stats", return_value={"total_albums": 10}),
            patch.object(orchestrator.api_service, "get_stats", return_value={"total_entries": 20}),
            patch.object(orchestrator.generic_service, "get_stats", return_value={"total_entries": 30}),
        ):
            stats = orchestrator.get_comprehensive_stats()

            assert "album_cache" in stats
            assert "api_cache" in stats
            assert "generic_cache" in stats
            assert "orchestrator" in stats
            assert stats["album_cache"]["total_albums"] == 10
            assert stats["api_cache"]["total_entries"] == 20
            assert stats["generic_cache"]["total_entries"] == 30

    @pytest.mark.asyncio
    async def test_get_cache_health(self) -> None:
        """Test that get_cache_health returns health status for all services."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.album_service, "get_stats", return_value={"total_albums": 5}),
            patch.object(orchestrator.api_service, "get_stats", return_value={"total_entries": 10}),
            patch.object(orchestrator.generic_service, "get_stats", return_value={"total_entries": 15}),
        ):
            health = orchestrator.get_cache_health()

            assert "album" in health
            assert "api" in health
            assert "generic" in health
            for service_health in health.values():
                assert service_health["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_get_cache_health_handles_errors(self) -> None:
        """Test that get_cache_health reports errors correctly."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.album_service, "get_stats", side_effect=Exception("Stats failed")),
            patch.object(orchestrator.api_service, "get_stats", return_value={"total_entries": 10}),
            patch.object(orchestrator.generic_service, "get_stats", return_value={"total_entries": 15}),
        ):
            health = orchestrator.get_cache_health()

            assert health["album"]["status"] == "error"
            assert "Stats failed" in health["album"]["error"]
            assert health["api"]["status"] == "healthy"

    # =========================== BACKWARD COMPATIBILITY TESTS ===========================

    @pytest.mark.asyncio
    async def test_cache_property(self) -> None:
        """Test that cache property returns generic service cache."""
        orchestrator = self.create_orchestrator()
        # Configure mock's cache attribute to return expected value
        orchestrator.generic_service.cache = {"key": ("value", 0.0)}

        # Access through property - the cache property delegates to generic_service.cache
        assert "key" in orchestrator.cache
        assert orchestrator.cache["key"] == ("value", 0.0)

    @pytest.mark.asyncio
    async def test_album_years_cache_property(self) -> None:
        """Test that album_years_cache property returns transformed data."""
        orchestrator = self.create_orchestrator()
        mock_entry = MagicMock()
        mock_entry.artist = "Artist"
        mock_entry.album = "Album"
        mock_entry.year = "1999"
        orchestrator.album_service.album_years_cache = {"key": mock_entry}

        result = orchestrator.album_years_cache

        assert "key" in result
        assert result["key"] == ("Artist", "Album", "1999")

    # =========================== PROTOCOL METHODS TESTS ===========================

    @pytest.mark.asyncio
    async def test_load_cache_noop(self) -> None:
        """Test that load_cache is a no-op (services load during init)."""
        orchestrator = self.create_orchestrator()

        # Should not raise
        await orchestrator.load_cache()

    @pytest.mark.asyncio
    async def test_save_cache_delegates(self) -> None:
        """Test that save_cache delegates to save_all_to_disk."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.album_service, "save_to_disk", new_callable=AsyncMock) as mock_album,
            patch.object(orchestrator.api_service, "save_to_disk", new_callable=AsyncMock),
            patch.object(orchestrator.generic_service, "save_to_disk", new_callable=AsyncMock),
        ):
            await orchestrator.save_cache()

            mock_album.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_last_run_timestamp(self) -> None:
        """Test that get_last_run_timestamp returns datetime."""
        orchestrator = self.create_orchestrator()

        with patch("services.cache.orchestrator.IncrementalRunTracker") as mock_tracker_class:
            mock_tracker = MagicMock()
            mock_tracker.get_last_run_timestamp = AsyncMock(return_value=datetime(2024, 1, 1, tzinfo=UTC))
            mock_tracker_class.return_value = mock_tracker

            result = await orchestrator.get_last_run_timestamp()

            assert result == datetime(2024, 1, 1, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_get_last_run_timestamp_never_run(self) -> None:
        """Test that get_last_run_timestamp returns epoch when never run."""
        orchestrator = self.create_orchestrator()

        with patch("services.cache.orchestrator.IncrementalRunTracker") as mock_tracker_class:
            mock_tracker = MagicMock()
            mock_tracker.get_last_run_timestamp = AsyncMock(return_value=None)
            mock_tracker_class.return_value = mock_tracker

            result = await orchestrator.get_last_run_timestamp()

            assert result == datetime(1970, 1, 1, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_generate_album_key(self) -> None:
        """Test that generate_album_key creates consistent keys."""
        key1 = CacheOrchestrator.generate_album_key("Artist", "Album")
        key2 = CacheOrchestrator.generate_album_key("Artist", "Album")
        key3 = CacheOrchestrator.generate_album_key("Other Artist", "Album")

        assert key1 == key2  # Same input -> same key
        assert key1 != key3  # Different input -> different key

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        """Test that clear() clears all caches."""
        orchestrator = self.create_orchestrator()

        with (
            patch.object(orchestrator.generic_service, "invalidate_all") as mock_generic,
            patch.object(orchestrator.album_service, "invalidate_all", new_callable=AsyncMock) as mock_album,
            patch.object(orchestrator.api_service, "invalidate_all", new_callable=AsyncMock) as mock_api,
        ):
            await orchestrator.clear()

            mock_generic.assert_called_once()
            mock_album.assert_called_once()
            mock_api.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown(self) -> None:
        """Test that shutdown stops cleanup tasks."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.generic_service, "stop_cleanup_task", new_callable=AsyncMock) as mock_stop:
            await orchestrator.shutdown()

            mock_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cached_api_result(self) -> None:
        """Test that get_cached_api_result delegates to api service."""
        orchestrator = self.create_orchestrator()
        mock_result = MagicMock()

        with patch.object(orchestrator.api_service, "get_cached_result", new_callable=AsyncMock, return_value=mock_result) as mock_get:
            result = await orchestrator.get_cached_api_result("Artist", "Album", "source")

            assert result == mock_result
            mock_get.assert_called_once_with("Artist", "Album", "source")

    @pytest.mark.asyncio
    async def test_set_cached_api_result(self) -> None:
        """Test that set_cached_api_result delegates to api service."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.api_service, "set_cached_result", new_callable=AsyncMock) as mock_set:
            await orchestrator.set_cached_api_result("Artist", "Album", "source", "2020", metadata={"extra": "data"})

            mock_set.assert_called_once()
            call_args = mock_set.call_args
            assert call_args[0][0] == "Artist"
            assert call_args[0][1] == "Album"
            assert call_args[0][2] == "source"

    @pytest.mark.asyncio
    async def test_set_cached_api_result_negative(self) -> None:
        """Test that set_cached_api_result handles negative results."""
        orchestrator = self.create_orchestrator()

        with patch.object(orchestrator.api_service, "set_cached_result", new_callable=AsyncMock) as mock_set:
            await orchestrator.set_cached_api_result("Artist", "Album", "source", None, is_negative=True)

            mock_set.assert_called_once()
            # success should be False for negative/None results
            call_args = mock_set.call_args
            assert call_args[0][3] is False  # success parameter
