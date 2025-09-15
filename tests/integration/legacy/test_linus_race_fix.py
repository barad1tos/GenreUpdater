#!/usr/bin/env python3

"""Tests for Linus race condition fix validation."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.services.cache.cache_service_wrapper import CacheServiceWrapper as CacheService


class TestLinusRaceFix:
    """Test race condition fix according to Linus recommendations."""

    @pytest.fixture
    def cache_service(self):
        """Create CacheService with temporary files."""
        console_logger = MagicMock()
        error_logger = MagicMock()
        
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "cache.json"
            csv_file = Path(temp_dir) / "cache_albums.csv"
            
            config = {
                "cache": {"cache_file": str(cache_file)},
                "albums": {"album_cache_csv": str(csv_file)}
            }
            
            service = CacheService(
                config=config,
                console_logger=console_logger,
                error_logger=error_logger
            )
            yield service

    async def test_single_executor_only(self, cache_service):
        """Test that only one executor is called - main race condition fix."""
        with patch('asyncio.get_event_loop') as mock_loop:
            mock_executor = AsyncMock()
            mock_loop.return_value.run_in_executor = mock_executor
            
            # Add data to both caches
            cache_service.cache = {"test": ("value", None)}
            cache_service.album_years_cache = {"hash": ("Artist", "Album", "2023")}
            
            await cache_service.save_cache()
            
            # Verify single executor call
            assert mock_executor.call_count == 1
            assert mock_executor.call_args[0][1] == cache_service.blocking_save_both

    def test_blocking_save_both_exists(self, cache_service):
        """Test that blocking_save_both method exists."""
        assert hasattr(cache_service, 'blocking_save_both')
        assert callable(cache_service.blocking_save_both)

    def test_alias_method_exists(self, cache_service):
        """Test that alias method _save_album_cache_sync exists."""
        assert hasattr(cache_service, '_save_album_cache_sync')
        assert callable(cache_service._save_album_cache_sync)

    async def test_empty_caches_early_return(self, cache_service):
        """Test early return when no data to save."""
        cache_service.cache = {}
        cache_service.album_years_cache = {}
        
        with patch('asyncio.get_event_loop') as mock_loop:
            mock_executor = AsyncMock()
            mock_loop.return_value.run_in_executor = mock_executor
            
            await cache_service.save_cache()
            
            # Executor should not be called
            mock_executor.assert_not_called()

    async def test_integration_both_caches_saved(self, cache_service):
        """Integration test - both caches are actually saved."""
        # Add data
        cache_service.cache = {"key": ("value", None)}
        cache_service.album_years_cache = {"hash": ("Artist", "Album", "2023")}
        
        # Save
        await cache_service.save_cache()
        
        # Verify files exist
        assert Path(cache_service.cache_file).exists()
        assert Path(cache_service.album_cache_csv).exists()


if __name__ == "__main__":
    pass
pytestmark = pytest.mark.integration
