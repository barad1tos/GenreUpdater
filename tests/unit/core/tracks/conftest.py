"""Shared fixtures for core/tracks tests."""

from __future__ import annotations

import logging

import pytest

from core.tracks.year_fallback import YearFallbackHandler
from tests.mocks.protocol_mocks import (
    MockExternalApiService,
    MockPendingVerificationService,
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
