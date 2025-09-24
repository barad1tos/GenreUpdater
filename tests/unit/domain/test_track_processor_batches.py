"""Test for TrackProcessor batch processing functionality."""

import logging
import types
from typing import Any

import pytest
from src.domain.tracks.track_processor import TrackProcessor
from src.shared.data.models import TrackDict
from src.shared.monitoring.analytics import Analytics, LoggerContainer


class DummyAppleScriptClient:
    """Mock AppleScript client for testing."""

    @staticmethod
    async def run_script(*_args: Any, **_kwargs: Any) -> str | None:
        """Mock run_script method that should not be called."""
        msg = "AppleScript should not be invoked in tests"
        raise AssertionError(msg)

    @staticmethod
    async def run_script_code(*_args: Any, **_kwargs: Any) -> str | None:
        """Mock run_script_code method that should not be called."""
        msg = "AppleScript should not be invoked in tests"
        raise AssertionError(msg)


class DummyCacheService:
    """Mock cache service for testing."""

    def __init__(self) -> None:
        """Initialize mock cache."""
        self.storage: dict[str, Any] = {}

    async def set_async(self, key_data: str, value: Any, _ttl: int | None = None) -> None:
        """Mock set_async method."""
        self.storage[key_data] = value

    async def get_async(self, key_data: str) -> Any | None:
        """Mock get_async method."""
        return self.storage.get(key_data)


@pytest.mark.asyncio
async def test_fetch_tracks_in_batches_populates_cache() -> None:
    """Test that batch processing populates cache correctly."""
    logger = logging.getLogger("test.track_processor.batch")
    logger.addHandler(logging.NullHandler())

    config: dict[str, Any] = {
        "analytics": {"duration_thresholds": {"short_max": 1, "medium_max": 5, "long_max": 10}},
        "development": {"test_artists": []},
    }
    analytics = Analytics(config, LoggerContainer(logger, logger, logger))
    cache_service = DummyCacheService()

    track_processor = TrackProcessor(
        ap_client=DummyAppleScriptClient(),  # type: ignore[arg-type]
        cache_service=cache_service,  # type: ignore[arg-type]
        console_logger=logger,
        error_logger=logger,
        config=config,
        analytics=analytics,
    )

    sample_tracks = [
        TrackDict(id="1", name="Song A", artist="Artist", album="Album", genre="Rock", year="2000"),
        TrackDict(id="2", name="Song B", artist="Artist", album="Album", genre="Rock", year="2001"),
    ]

    async def fake_process_single_batch(_self: TrackProcessor, batch_count: int, offset: int, batch_size: int) -> tuple[list[TrackDict], bool] | None:
        """Mock single batch processing."""
        assert batch_count == 1
        assert offset == 1
        assert batch_size == 50
        return sample_tracks, False

    # Mock private method for testing - accessing private member is acceptable in tests
    track_processor._process_single_batch = types.MethodType(fake_process_single_batch, track_processor)  # type: ignore[method-assign] # noqa: SLF001

    tracks_loaded = await track_processor.fetch_tracks_in_batches(50)
    assert tracks_loaded == sample_tracks
    assert cache_service.storage["tracks_all"] == sample_tracks

    async def forbid_fetch_from_applescript(*_args: Any, **_kwargs: Any) -> None:
        """Mock method that should not be called when using cache."""
        msg = "Should use cached snapshot instead of AppleScript"
        raise AssertionError(msg)

    # Mock private method for testing - accessing private member is acceptable in tests
    track_processor._fetch_tracks_from_applescript = types.MethodType(forbid_fetch_from_applescript, track_processor)  # type: ignore[method-assign] # noqa: SLF001

    cached_result = await track_processor.fetch_tracks_async()
    assert [track.id for track in cached_result] == [track.id for track in sample_tracks]
