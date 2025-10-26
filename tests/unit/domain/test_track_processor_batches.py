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

    async def fake_process_single_batch(_self: TrackProcessor, batch_count: int, offset: int, batch_size: int) -> tuple[list[TrackDict], bool, bool] | None:
        """Mock single batch processing."""
        assert batch_count == 1
        assert offset == 1
        assert batch_size == 50
        return sample_tracks, False, False  # tracks, should_continue, parse_failed

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


@pytest.mark.asyncio
async def test_apply_track_updates_individual_fallback() -> None:
    """Test that _apply_track_updates falls back to individual updates when batch is disabled."""
    logger = logging.getLogger("test.track_processor.batch_fallback")
    logger.addHandler(logging.NullHandler())

    config: dict[str, Any] = {
        "analytics": {"duration_thresholds": {"short_max": 1, "medium_max": 5, "long_max": 10}},
        "development": {"test_artists": []},
        "experimental": {"batch_updates_enabled": False},  # Disabled
    }
    analytics = Analytics(config, LoggerContainer(logger, logger, logger))
    cache_service = DummyCacheService()

    # Mock AppleScript client for individual updates
    mock_ap_client = DummyAppleScriptClient()
    update_calls = []

    async def mock_run_script(script_name: str, args: list[str], **_kwargs: Any) -> str:
        """Mock individual update script calls."""
        if script_name == "update_property.applescript":
            update_calls.append((script_name, args))
            return "Success: Property updated"
        msg = f"Unexpected script call: {script_name}"
        raise AssertionError(msg)

    mock_ap_client.run_script = mock_run_script  # type: ignore[method-assign]

    track_processor = TrackProcessor(
        ap_client=mock_ap_client,  # type: ignore[arg-type]
        cache_service=cache_service,  # type: ignore[arg-type]
        console_logger=logger,
        error_logger=logger,
        config=config,
        analytics=analytics,
    )

    # Test multiple updates on single track
    updates = [("genre", "Rock"), ("year", "2020")]
    result = await track_processor._apply_track_updates("123", updates)  # type: ignore[attr-defined] # noqa: SLF001

    assert result is True
    assert len(update_calls) == 2  # Should make individual calls
    assert update_calls[0] == ("update_property.applescript", ["123", "genre", "Rock"])
    assert update_calls[1] == ("update_property.applescript", ["123", "year", "2020"])


@pytest.mark.asyncio
async def test_apply_track_updates_batch_success() -> None:
    """Test successful batch update when enabled."""
    logger = logging.getLogger("test.track_processor.batch_success")
    logger.addHandler(logging.NullHandler())

    config: dict[str, Any] = {
        "analytics": {"duration_thresholds": {"short_max": 1, "medium_max": 5, "long_max": 10}},
        "development": {"test_artists": []},
        "experimental": {"batch_updates_enabled": True, "max_batch_size": 5},
    }
    analytics = Analytics(config, LoggerContainer(logger, logger, logger))
    cache_service = DummyCacheService()

    mock_ap_client = DummyAppleScriptClient()
    batch_calls = []

    async def mock_run_script(script_name: str, args: list[str], **_kwargs: Any) -> str:
        """Mock batch update script call."""
        if script_name == "batch_update_tracks.applescript":
            batch_calls.append((script_name, args))
            return "Success: Batch update process completed."
        msg = f"Unexpected script call: {script_name}"
        raise AssertionError(msg)

    mock_ap_client.run_script = mock_run_script  # type: ignore[method-assign]

    track_processor = TrackProcessor(
        ap_client=mock_ap_client,  # type: ignore[arg-type]
        cache_service=cache_service,  # type: ignore[arg-type]
        console_logger=logger,
        error_logger=logger,
        config=config,
        analytics=analytics,
    )

    # Test batch updates
    updates = [("genre", "Rock"), ("year", "2020")]
    result = await track_processor._apply_track_updates("123", updates)  # type: ignore[attr-defined] # noqa: SLF001

    assert result is True
    assert len(batch_calls) == 1
    batch_command = batch_calls[0][1][0]
    assert batch_command == "123:genre:Rock;123:year:2020"


@pytest.mark.asyncio
async def test_apply_track_updates_batch_fallback() -> None:
    """Test fallback to individual updates when batch fails."""
    logger = logging.getLogger("test.track_processor.batch_fallback")
    logger.addHandler(logging.NullHandler())

    config: dict[str, Any] = {
        "analytics": {"duration_thresholds": {"short_max": 1, "medium_max": 5, "long_max": 10}},
        "development": {"test_artists": []},
        "experimental": {"batch_updates_enabled": True, "max_batch_size": 5},
    }
    analytics = Analytics(config, LoggerContainer(logger, logger, logger))
    cache_service = DummyCacheService()

    mock_ap_client = DummyAppleScriptClient()
    script_calls = []

    async def mock_run_script(script_name: str, args: list[str], **_kwargs: Any) -> str:
        """Mock script calls with batch failure."""
        script_calls.append((script_name, args))
        if script_name == "batch_update_tracks.applescript":
            return "Error: Track not found"  # Batch fails
        if script_name == "update_property.applescript":
            return "Success: Property updated"  # Individual succeeds
        msg = f"Unexpected script call: {script_name}"
        raise AssertionError(msg)

    mock_ap_client.run_script = mock_run_script  # type: ignore[method-assign]

    track_processor = TrackProcessor(
        ap_client=mock_ap_client,  # type: ignore[arg-type]
        cache_service=cache_service,  # type: ignore[arg-type]
        console_logger=logger,
        error_logger=logger,
        config=config,
        analytics=analytics,
    )

    updates = [("genre", "Rock"), ("year", "2020")]
    result = await track_processor._apply_track_updates("123", updates)  # type: ignore[attr-defined] # noqa: SLF001

    assert result is True
    # Should have tried batch first, then fallen back to individual calls
    assert len(script_calls) == 3  # 1 batch + 2 individual
    assert script_calls[0][0] == "batch_update_tracks.applescript"
    assert script_calls[1][0] == "update_property.applescript"
    assert script_calls[2][0] == "update_property.applescript"
