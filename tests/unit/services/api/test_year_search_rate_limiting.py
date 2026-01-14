"""Tests for YearSearchCoordinator rate limiting with semaphore.

Verifies that concurrent API requests are limited by a semaphore
to prevent socket exhaustion on large libraries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.api.year_search_coordinator import YearSearchCoordinator


def _create_mock_client() -> MagicMock:
    """Create a mock API client."""
    client = MagicMock()
    client.search_release = AsyncMock(return_value=[])
    client.get_scored_releases = AsyncMock(return_value=[])
    return client


def _create_coordinator(
    max_concurrent_api_calls: int = 50,
    config: dict[str, Any] | None = None,
) -> YearSearchCoordinator:
    """Create YearSearchCoordinator with mock dependencies."""
    return YearSearchCoordinator(
        console_logger=logging.getLogger("test.console"),
        error_logger=logging.getLogger("test.error"),
        config=config or {},
        preferred_api="musicbrainz",
        musicbrainz_client=_create_mock_client(),
        discogs_client=_create_mock_client(),
        applemusic_client=_create_mock_client(),
        release_scorer=MagicMock(),
        max_concurrent_api_calls=max_concurrent_api_calls,
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestYearSearchRateLimiting:
    """Tests for API request rate limiting with semaphore."""

    async def test_coordinator_has_semaphore_attribute(self) -> None:
        """Coordinator should have a semaphore for rate limiting."""
        coordinator = _create_coordinator(max_concurrent_api_calls=10)

        assert hasattr(coordinator, "_api_semaphore")
        assert isinstance(coordinator._api_semaphore, asyncio.Semaphore)

    async def test_semaphore_uses_configured_limit(self) -> None:
        """Semaphore should use the configured max concurrent calls."""
        coordinator = _create_coordinator(max_concurrent_api_calls=5)
        semaphore = coordinator._api_semaphore

        # Behavioral test: acquire limit times should succeed, limit+1 should block
        # Acquire 5 times (the configured limit)
        for _ in range(5):
            acquired = await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
            assert acquired

        # 6th acquire should timeout (semaphore is exhausted)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(semaphore.acquire(), timeout=0.1)

        # Cleanup: release all acquired permits
        for _ in range(5):
            semaphore.release()

    async def test_api_calls_respect_semaphore_limit(self) -> None:
        """API calls should not exceed the semaphore limit."""
        max_concurrent = 2
        coordinator = _create_coordinator(max_concurrent_api_calls=max_concurrent)

        # Track concurrent execution
        active_count = 0
        max_active = 0
        lock = asyncio.Lock()

        async def slow_api_call(*args: Any, **kwargs: Any) -> list[Any]:
            """Mock API call that tracks concurrency."""
            _ = args, kwargs  # Mark as intentionally unused
            nonlocal active_count, max_active
            async with lock:
                active_count += 1
                max_active = max(max_active, active_count)
            await asyncio.sleep(0.05)  # Simulate network delay
            async with lock:
                active_count -= 1
            return []

        # Patch all API clients to use our slow mock
        coordinator.musicbrainz_client.get_scored_releases = AsyncMock(side_effect=slow_api_call)
        coordinator.discogs_client.get_scored_releases = AsyncMock(side_effect=slow_api_call)
        coordinator.applemusic_client.get_scored_releases = AsyncMock(side_effect=slow_api_call)

        # Execute multiple searches concurrently
        search_tasks = [
            coordinator.fetch_all_api_results(
                artist_norm=f"artist{i}",
                album_norm=f"album{i}",
                artist_region=None,
                log_artist=f"Artist {i}",
                log_album=f"Album {i}",
            )
            for i in range(6)  # 6 searches, each with 3 API calls
        ]

        await asyncio.gather(*search_tasks)

        # Max concurrent should not exceed limit
        # Note: Each search triggers multiple API calls, but semaphore limits total
        assert max_active <= max_concurrent, f"Expected max {max_concurrent} concurrent calls, but saw {max_active}"

    async def test_default_semaphore_limit(self) -> None:
        """Default semaphore limit should be 50."""
        # Create coordinator without specifying limit
        coordinator = YearSearchCoordinator(
            console_logger=logging.getLogger("test.console"),
            error_logger=logging.getLogger("test.error"),
            config={},
            preferred_api="musicbrainz",
            musicbrainz_client=_create_mock_client(),
            discogs_client=_create_mock_client(),
            applemusic_client=_create_mock_client(),
            release_scorer=MagicMock(),
        )
        semaphore = coordinator._api_semaphore

        # Behavioral test: verify default limit is 50 by acquiring all permits
        acquired_count = 0
        for _ in range(50):
            try:
                acquired = await asyncio.wait_for(semaphore.acquire(), timeout=0.01)
                if acquired:
                    acquired_count += 1
            except TimeoutError:
                break

        # Should have acquired exactly 50 permits
        assert acquired_count == 50, f"Expected 50 permits, got {acquired_count}"

        # 51st acquire should timeout (semaphore is exhausted)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(semaphore.acquire(), timeout=0.01)

        # Cleanup: release all acquired permits
        for _ in range(acquired_count):
            semaphore.release()
