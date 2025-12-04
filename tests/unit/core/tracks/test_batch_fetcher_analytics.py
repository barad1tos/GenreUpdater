"""Tests for BatchTrackFetcher analytics integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models.track_models import TrackDict
from src.core.tracks.batch_fetcher import BatchTrackFetcher
from src.metrics.analytics import Analytics, LoggerContainer

if TYPE_CHECKING:
    pass


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
    config = {"analytics": {"enabled": True}}
    return Analytics(config, analytics_loggers)


@pytest.fixture
def config() -> dict[str, Any]:
    """Create test config."""
    return {
        "batch_processing": {"ids_batch_size": 100},
    }


def create_batch_fetcher(
    ap_client: MagicMock,
    cache_service: MagicMock,
    loggers: tuple[logging.Logger, logging.Logger],
    config: dict[str, Any],
    analytics: Analytics | None = None,
) -> BatchTrackFetcher:
    """Factory to create BatchTrackFetcher with common dependencies."""
    console_logger, error_logger = loggers
    return BatchTrackFetcher(
        ap_client=ap_client,
        cache_service=cache_service,
        console_logger=console_logger,
        error_logger=error_logger,
        config=config,
        track_validator=lambda x: x,
        artist_processor=AsyncMock(),
        snapshot_loader=AsyncMock(return_value=None),
        snapshot_persister=AsyncMock(),
        can_use_snapshot=lambda x: False,
        dry_run=False,
        analytics=analytics,
    )


class TestBatchFetcherInit:
    """Tests for BatchTrackFetcher initialization."""

    def test_init_without_analytics(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: dict[str, Any],
    ) -> None:
        """Should initialize without analytics parameter."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config, analytics=None
        )
        assert fetcher.analytics is None

    def test_init_with_analytics(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: dict[str, Any],
        analytics: Analytics,
    ) -> None:
        """Should initialize with analytics parameter."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config, analytics=analytics
        )
        assert fetcher.analytics is analytics


class TestFetchTracksInBatchesRouting:
    """Tests for _fetch_tracks_in_batches routing logic."""

    @pytest.mark.asyncio
    async def test_uses_analytics_method_when_analytics_available(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: dict[str, Any],
        analytics: Analytics,
    ) -> None:
        """Should use analytics batch mode when analytics is available."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config, analytics=analytics
        )

        # Mock to return empty to stop loop
        mock_ap_client.run_script = AsyncMock(return_value=None)

        with patch.object(
            fetcher, "_fetch_tracks_in_batches_with_analytics", new_callable=AsyncMock
        ) as mock_with_analytics:
            mock_with_analytics.return_value = []
            await fetcher._fetch_tracks_in_batches(100)
            mock_with_analytics.assert_called_once_with(100)

    @pytest.mark.asyncio
    async def test_uses_raw_method_when_no_analytics(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: dict[str, Any],
    ) -> None:
        """Should use raw method when analytics is not available."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config, analytics=None
        )

        with patch.object(
            fetcher, "_fetch_tracks_in_batches_raw", new_callable=AsyncMock
        ) as mock_raw:
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
        config: dict[str, Any],
        analytics: Analytics,
    ) -> None:
        """Should use analytics.batch_mode context manager."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config, analytics=analytics
        )

        # Track suppress state changes
        suppress_states_during_run: list[bool] = []

        async def capture_suppress_state(*args: Any, **kwargs: Any) -> None:
            suppress_states_during_run.append(analytics._suppress_console_logging)
            return None

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
        config: dict[str, Any],
        analytics: Analytics,
    ) -> None:
        """Should suppress console logging while fetching."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config, analytics=analytics
        )

        suppress_states: list[bool] = []

        async def capture_state(*args: Any, **kwargs: Any) -> None:
            suppress_states.append(analytics._suppress_console_logging)
            return None

        mock_ap_client.run_script = AsyncMock(side_effect=capture_state)
        await fetcher._fetch_tracks_in_batches_with_analytics(100)

        # During execution, suppression should have been True
        if suppress_states:
            assert all(suppress_states), "Console logging should be suppressed during batch fetch"


class TestFetchTracksRawFallback:
    """Tests for batch fetching without analytics (raw fallback)."""

    @pytest.mark.asyncio
    async def test_works_without_analytics(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: dict[str, Any],
    ) -> None:
        """Should work correctly without analytics."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config, analytics=None
        )

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
        config: dict[str, Any],
    ) -> None:
        """Should use Rich Console status for progress display."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config, analytics=None
        )

        mock_ap_client.run_script = AsyncMock(return_value=None)

        with patch("src.core.tracks.batch_fetcher.Console") as mock_console_class:
            mock_console = MagicMock()
            mock_status = MagicMock()
            mock_status.__enter__ = MagicMock(return_value=mock_status)
            mock_status.__exit__ = MagicMock(return_value=None)
            mock_console.status.return_value = mock_status
            mock_console_class.return_value = mock_console

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
        config: dict[str, Any],
    ) -> None:
        """Should return None when batch returns no results."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config
        )

        mock_ap_client.run_script = AsyncMock(return_value=None)

        result = await fetcher._process_single_batch(1, 1, 100, 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: dict[str, Any],
    ) -> None:
        """Should return None and log error on exception."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config
        )

        mock_ap_client.run_script = AsyncMock(side_effect=OSError("Test error"))

        result = await fetcher._process_single_batch(1, 1, 100, 0)
        assert result is None

        # Error should be logged
        console_logger, error_logger = loggers
        error_logger.exception.assert_called()

    @pytest.mark.asyncio
    async def test_returns_tuple_on_success(
        self,
        mock_ap_client: MagicMock,
        mock_cache_service: MagicMock,
        loggers: tuple[logging.Logger, logging.Logger],
        config: dict[str, Any],
    ) -> None:
        """Should return (tracks, new_offset, failures, continue) on success."""
        fetcher = create_batch_fetcher(
            mock_ap_client, mock_cache_service, loggers, config
        )

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
