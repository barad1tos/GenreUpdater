"""Integration tests covering core behavior of CacheOrchestrator."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from services.cache.orchestrator import CacheOrchestrator

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.asyncio


@pytest.fixture
def cache_config() -> dict[str, Any]:
    """Return a minimal cache configuration for testing."""
    temp_dir = tempfile.mkdtemp()
    return {
        "caching": {
            "default_ttl_seconds": 5,
            "cleanup_interval_seconds": 1,
            "api_result_cache_path": str(Path(temp_dir) / "api_results.json"),
            "album_cache_sync_interval": 1,
        },
        "api_cache_file": str(Path(temp_dir) / "cache.json"),
        "logging": {
            "base_dir": temp_dir,
        },
    }


@pytest.fixture
def cache_logger() -> logging.Logger:
    """Provide a quiet logger instance for cache tests."""
    logger = logging.getLogger("test.cache_orchestrator")
    logger.handlers = []
    logger.propagate = False
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
async def orchestrator(cache_config: dict[str, Any], cache_logger: logging.Logger) -> AsyncGenerator[CacheOrchestrator]:
    """Yield an initialized CacheOrchestrator instance."""
    orchestrator = CacheOrchestrator(cache_config, cache_logger)
    await orchestrator.initialize()
    try:
        yield orchestrator
    finally:
        await orchestrator.invalidate_all()


async def test_generic_set_and_get(orchestrator: CacheOrchestrator) -> None:
    """Generic cache supports synchronous set and asynchronous get operations."""
    orchestrator.set("test_key", "value", ttl=5)
    assert await orchestrator.get_async("test_key") == "value"


async def test_async_compute_on_miss(orchestrator: CacheOrchestrator) -> None:
    """When a key is missing, compute callback is evaluated and cached."""

    def compute() -> asyncio.Future[Any]:
        """Compute function that returns a test value."""

        async def _compute() -> str:
            await asyncio.sleep(0)
            return "computed"

        return asyncio.create_task(_compute())

    result = await orchestrator.get_async("missing", compute)
    assert result == "computed"
    # subsequent get should use cached value without executing compute
    assert await orchestrator.get_async("missing") == "computed"


async def test_invalidate_single_key(orchestrator: CacheOrchestrator) -> None:
    """Invalidating a key removes it from the cache."""
    orchestrator.set("to_remove", "keep_me")
    orchestrator.invalidate("to_remove")
    assert await orchestrator.get_async("to_remove") is None


async def test_zero_ttl_expires_immediately(orchestrator: CacheOrchestrator) -> None:
    """Entries with zero TTL should expire immediately on retrieval."""
    orchestrator.set("ttl_key", "short", ttl=0)
    assert await orchestrator.get_async("ttl_key") is None


async def test_album_year_roundtrip(orchestrator: CacheOrchestrator) -> None:
    """Album year storage and retrieval delegate to album cache service."""
    await orchestrator.store_album_year("Artist", "Album", "1999")
    assert await orchestrator.get_album_year("Artist", "Album") == "1999"
