"""Tests for BatchTrackFetcher analytics integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from core.models.protocols import CacheServiceProtocol
    from core.models.track_models import AppConfig
    from core.models.types import AppleScriptClientProtocol

from core.tracks.batch_fetcher import BatchTrackFetcher
from metrics.analytics import Analytics, LoggerContainer
from tests.factories import create_test_app_config  # sourcery skip: dont-import-test-modules


@pytest.fixture
def mock_ap_client() -> MagicMock:
    """Create mock AppleScript client."""
    client = MagicMock()
    client.run_script = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_cache_service() -> MagicMock:
    """Create mock cache service."""
    service = MagicMock()
    service.set_async = AsyncMock()
    return service


@pytest.fixture
def loggers() -> tuple[logging.Logger, logging.Logger]:
    """Create mock loggers."""
    console = MagicMock(spec=logging.Logger)
    error = MagicMock(spec=logging.Logger)
    return console, error


@pytest.fixture
def analytics_loggers() -> LoggerContainer:
    """Create LoggerContainer for Analytics."""
    return LoggerContainer(
        MagicMock(spec=logging.Logger),
        MagicMock(spec=logging.Logger),
        MagicMock(spec=logging.Logger),
    )


@pytest.fixture
def analytics(analytics_loggers: LoggerContainer) -> Analytics:
    """Create Analytics instance."""
    return Analytics(create_test_app_config(), analytics_loggers)


@pytest.fixture
def config() -> AppConfig:
    """Create test config."""
    return create_test_app_config()


def create_batch_fetcher(
    ap_client: MagicMock,
    cache_service: MagicMock,
    loggers: tuple[logging.Logger, logging.Logger],
    config: AppConfig,
    analytics: Analytics | None = None,
) -> BatchTrackFetcher:
    """Factory to create BatchTrackFetcher with common dependencies."""
    console_logger, error_logger = loggers
    return BatchTrackFetcher(
        ap_client=cast("AppleScriptClientProtocol", cast(object, ap_client)),
        cache_service=cast("CacheServiceProtocol", cast(object, cache_service)),
        console_logger=console_logger,
        error_logger=error_logger,
        config=config,
        track_validator=lambda x: x,
        artist_processor=AsyncMock(),
        snapshot_loader=AsyncMock(return_value=None),
        snapshot_persister=AsyncMock(),
        can_use_snapshot=lambda x: False,
        analytics=analytics,
    )


class TestBatchFetcherInit:
    """Tests for BatchTrackFetcher initialization."""

    def test_init_without_analytics(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Should initialize without analytics parameter."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config)
        assert fetcher.analytics is None

    def test_init_with_analytics(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
        analytics: Analytics,
    ) -> None:
        """Should initialize with analytics parameter."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config, analytics=analytics)
        assert fetcher.analytics is analytics


class TestFetchTracksInBatchesRouting:
    """Tests for _fetch_tracks_in_batches routing logic."""

    @pytest.mark.asyncio
    async def test_uses_analytics_method_when_analytics_available(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
        analytics: Analytics,
    ) -> None:
        """Should use analytics batch mode when analytics is available."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config, analytics=analytics)

        # Mock to return empty to stop loop
        mock_ap_client.run_script = AsyncMock(return_value=None)

        with patch.object(fetcher, "_fetch_tracks_in_batches_with_analytics", new_callable=AsyncMock) as mock_with_analytics:
            mock_with_analytics.return_value = []
            await fetcher._fetch_tracks_in_batches(100)
            mock_with_analytics.assert_called_once_with(100)

    @pytest.mark.asyncio
    async def test_uses_raw_method_when_no_analytics(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Should use raw method when analytics is not available."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config)

        with patch.object(fetcher, "_fetch_tracks_in_batches_raw", new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = []
            await fetcher._fetch_tracks_in_batches(100)
            mock_raw.assert_called_once_with(100)


class TestFetchTracksWithAnalytics:
    """Tests for batch fetching with analytics batch_mode."""

    @pytest.mark.asyncio
    async def test_uses_batch_mode_context(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
        analytics: Analytics,
    ) -> None:
        """Should use analytics.batch_mode context manager."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config, analytics=analytics)

        # Track suppress state changes
        suppress_states_during_run: list[bool] = []

        async def capture_suppress_state(*_args: Any, **_kwargs: Any) -> None:
            """Capture console logging suppression state during run_script call."""
            suppress_states_during_run.append(analytics._suppress_console_logging)

        mock_ap_client.run_script = AsyncMock(side_effect=capture_suppress_state)

        # Before: should be False
        assert analytics._suppress_console_logging is False

        await fetcher._fetch_tracks_in_batches_with_analytics(100)

        # After: should be False again
        assert analytics._suppress_console_logging is False

        # During run_script call, it should have been True
        if suppress_states_during_run:
            assert suppress_states_during_run[0] is True

    @pytest.mark.asyncio
    async def test_suppresses_console_logging_during_fetch(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
        analytics: Analytics,
    ) -> None:
        """Should suppress console logging while fetching."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config, analytics=analytics)

        suppress_states: list[bool] = []

        async def capture_state(*_args: Any, **_kwargs: Any) -> None:
            """Capture console logging suppression state during execution."""
            suppress_states.append(analytics._suppress_console_logging)

        mock_ap_client.run_script = AsyncMock(side_effect=capture_state)
        await fetcher._fetch_tracks_in_batches_with_analytics(100)

        # During execution, suppression should have been True
        if suppress_states:
            assert all(suppress_states), "Console logging should be suppressed during batch fetch"


class TestAnalyticsGuard:
    """Tests for the analytics None guard in _fetch_tracks_in_batches_with_analytics."""

    @pytest.mark.asyncio
    async def test_raises_when_analytics_is_none(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Guard raises RuntimeError when analytics is None."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config)
        assert fetcher.analytics is None

        with pytest.raises(RuntimeError, match="analytics must be initialized"):
            await fetcher._fetch_tracks_in_batches_with_analytics(100)


class TestFetchTracksRawFallback:
    """Tests for batch fetching without analytics (raw fallback)."""

    @pytest.mark.asyncio
    async def test_works_without_analytics(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Should work correctly without analytics."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config)

        mock_ap_client.run_script = AsyncMock(return_value=None)

        # Should not raise
        result = await fetcher._fetch_tracks_in_batches_raw(100)
        assert result == []

    @pytest.mark.asyncio
    async def test_uses_rich_console_status(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Should use Rich Console status for progress display."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config)

        mock_ap_client.run_script = AsyncMock(return_value=None)

        with patch("core.tracks.batch_fetcher.get_shared_console") as mock_get_console:
            mock_console = MagicMock()
            mock_status = MagicMock()
            mock_status.__enter__ = MagicMock(return_value=mock_status)
            mock_status.__exit__ = MagicMock(return_value=None)
            mock_console.status.return_value = mock_status
            mock_get_console.return_value = mock_console

            await fetcher._fetch_tracks_in_batches_raw(100)

            mock_console.status.assert_called()


class TestProcessSingleBatch:
    """Tests for _process_single_batch helper."""

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_result(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Should return None when batch returns no results."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config)

        mock_ap_client.run_script = AsyncMock(return_value=None)

        result = await fetcher._process_single_batch(1, 1, 100, 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Should return None and log error on exception."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config)

        mock_ap_client.run_script = AsyncMock(side_effect=OSError("Test error"))

        result = await fetcher._process_single_batch(1, 1, 100, 0)
        assert result is None

        # Error should be logged
        _console_logger, error_logger = loggers
        exception_mock = cast(MagicMock, error_logger.exception)
        exception_mock.assert_called()

    @pytest.mark.asyncio
    async def test_returns_tuple_on_success(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Should return (tracks, new_offset, failures, continue) on success."""
        fetcher = create_batch_fetcher(mock_ap_client, mock_cache_service, loggers, config)

        # Create valid track output with all required fields
        # Fields: ID, NAME, ARTIST, ALBUM_ARTIST, ALBUM, GENRE, DATE_ADDED
        # Note: \x1e is field separator, \x1d is line/record separator (required to avoid splitlines bug)
        track_output = "1\x1eTrack 1\x1eArtist 1\x1eAlbum Artist 1\x1eAlbum 1\x1eRock\x1e2024-01-01 12:00:00\x1d"
        mock_ap_client.run_script = AsyncMock(return_value=track_output)

        result = await fetcher._process_single_batch(1, 1, 100, 0)

        assert result is not None
        tracks, new_offset, new_failures, should_continue = result
        assert isinstance(tracks, list)
        assert new_offset == 101  # 1 + 100
        # new_failures should be 0 since parse succeeded (not a parse failure)
        assert new_failures == 0
        assert should_continue is True


class TestCacheAndPersistResults:
    """Tests for _cache_and_persist_results snapshot error handling."""

    @pytest.mark.asyncio
    async def test_snapshot_persist_error_is_logged(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: AppConfig,
    ) -> None:
        """Should log warning when snapshot persistence raises OSError."""
        failing_persister = AsyncMock(side_effect=OSError("Disk full"))
        fetcher = BatchTrackFetcher(
            ap_client=cast("AppleScriptClientProtocol", cast(object, mock_ap_client)),
            cache_service=cast("CacheServiceProtocol", cast(object, mock_cache_service)),
            console_logger=loggers[0],
            error_logger=loggers[1],
            config=config,
            track_validator=lambda x: x,
            artist_processor=AsyncMock(),
            snapshot_loader=AsyncMock(return_value=None),
            snapshot_persister=failing_persister,
            can_use_snapshot=lambda _x: True,
        )

        track = MagicMock()
        track.id = "1"
        tracks_arg = cast(Any, [track])
        await fetcher._cache_and_persist_results(tracks_arg)

        cast(MagicMock, loggers[1]).warning.assert_called_once()
        assert "Disk full" in str(cast(MagicMock, loggers[1]).warning.call_args)
