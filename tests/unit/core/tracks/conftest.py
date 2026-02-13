"""Shared fixtures for core/tracks tests."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.protocols import AnalyticsProtocol
from core.models.types import TrackDict
from core.tracks.year_batch import YearBatchProcessor
from core.tracks.year_fallback import YearFallbackHandler
from tests.factories import create_test_app_config  # sourcery skip: dont-import-test-modules
from tests.mocks.protocol_mocks import (  # sourcery skip: dont-import-test-modules
    MockExternalApiService,
    MockPendingVerificationService,
)

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


# ---------------------------------------------------------------------------
# Track factory (helper function, not a fixture)
# ---------------------------------------------------------------------------


def create_test_track(
    track_id: str = "1",
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    genre: str | None = None,
    year: str | None = None,
    date_added: str | None = None,
    last_modified: str | None = None,
    track_status: str | None = None,
    year_before_mgu: str | None = None,
    year_set_by_mgu: str | None = None,
    release_year: str | None = None,
) -> TrackDict:
    """Unified test track factory for core/tracks tests.

    Covers all optional TrackDict fields used across year_batch test files.
    """
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        genre=genre,
        year=year,
        date_added=date_added,
        last_modified=last_modified,
        track_status=track_status,
        year_before_mgu=year_before_mgu,
        year_set_by_mgu=year_set_by_mgu,
        release_year=release_year,
    )


# ---------------------------------------------------------------------------
# Shared mock factories for YearBatchProcessor
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_track_processor() -> MagicMock:
    """Create a mock track processor for YearBatchProcessor."""
    processor = MagicMock()
    processor.update_tracks_batch_async = AsyncMock(return_value=[])
    processor.update_property = AsyncMock(return_value=(True, True))
    return processor


def create_year_determinator_mock() -> MagicMock:
    """Build a MagicMock year-determinator with all async stubs."""
    determinator = MagicMock()
    determinator.should_skip_album = AsyncMock(return_value=(False, None))
    determinator.determine_album_year = AsyncMock(return_value="2020")
    determinator.check_prerelease_status = AsyncMock(return_value=False)
    determinator.check_suspicious_album = AsyncMock(return_value=False)
    determinator.handle_future_years = AsyncMock(return_value=False)
    determinator.extract_future_years = MagicMock(return_value=[])
    # Always set: YearBatchProcessor.__init__ passes these to PrereleaseHandler
    determinator.pending_verification = MagicMock()
    determinator.pending_verification.mark_for_verification = AsyncMock()
    determinator.prerelease_recheck_days = 30
    return determinator


@pytest.fixture
def mock_year_determinator() -> MagicMock:
    """Create a mock year determinator for YearBatchProcessor (with pending_verification)."""
    return create_year_determinator_mock()


@pytest.fixture
def mock_retry_handler() -> MagicMock:
    """Create a mock retry handler for YearBatchProcessor."""
    handler = MagicMock()
    handler.execute_with_retry = AsyncMock()
    return handler


@pytest.fixture
def mock_analytics() -> MagicMock:
    """Create a mock analytics instance for YearBatchProcessor."""
    return MagicMock()


def create_year_batch_processor(
    *,
    track_processor: MagicMock | None = None,
    year_determinator: MagicMock | None = None,
    retry_handler: MagicMock | None = None,
    analytics: MagicMock | None = None,
    console_logger: logging.Logger | None = None,
    error_logger: logging.Logger | None = None,
    config: AppConfig | None = None,
    dry_run: bool = False,
) -> YearBatchProcessor:
    """Create YearBatchProcessor with mock dependencies.

    Any dependency not provided will get a sensible default mock.
    """
    if track_processor is None:
        tp = MagicMock()
        tp.update_tracks_batch_async = AsyncMock(return_value=[])
        track_processor = tp
    if year_determinator is None:
        year_determinator = create_year_determinator_mock()
    if retry_handler is None:
        retry_handler = MagicMock()
        retry_handler.execute_with_retry = AsyncMock()
    return YearBatchProcessor(
        track_processor=track_processor,
        year_determinator=year_determinator,
        retry_handler=retry_handler,
        console_logger=console_logger or logging.getLogger("test.console"),
        error_logger=error_logger or logging.getLogger("test.error"),
        config=config or create_test_app_config(),
        analytics=analytics or cast(AnalyticsProtocol, cast(object, MagicMock())),
        dry_run=dry_run,
    )


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create a test console logger."""
    return logging.getLogger("test.plausibility.console")


@pytest.fixture
def mock_pending_verification() -> MockPendingVerificationService:
    """Create mock pending verification service."""
    return MockPendingVerificationService()


@pytest.fixture
def mock_api_orchestrator() -> MockExternalApiService:
    """Create mock API orchestrator with get_artist_start_year."""
    mock = MockExternalApiService()
    mock.artist_activity_response = (None, None)  # Default: no artist data
    return mock


@pytest.fixture
def fallback_handler(
    console_logger: logging.Logger,
    mock_pending_verification: MockPendingVerificationService,
    mock_api_orchestrator: MockExternalApiService,
) -> YearFallbackHandler:
    """Create YearFallbackHandler with mocked dependencies."""
    return YearFallbackHandler(
        console_logger=console_logger,
        pending_verification=mock_pending_verification,
        fallback_enabled=True,
        absurd_year_threshold=1900,
        year_difference_threshold=5,
        trust_api_score_threshold=70,
        api_orchestrator=mock_api_orchestrator,
    )


@pytest.fixture
def fallback_handler_no_orchestrator(
    console_logger: logging.Logger,
    mock_pending_verification: MockPendingVerificationService,
) -> YearFallbackHandler:
    """Create YearFallbackHandler without api_orchestrator."""
    return YearFallbackHandler(
        console_logger=console_logger,
        pending_verification=mock_pending_verification,
        fallback_enabled=True,
        absurd_year_threshold=1900,
        year_difference_threshold=5,
        trust_api_score_threshold=70,
    )
