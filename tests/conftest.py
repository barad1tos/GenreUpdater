"""Pytest configuration and shared fixtures for Genres Autoupdater v2.0.

This module configures the test environment by ensuring the project root
is added to sys.path, allowing imports of the src package modules.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, settings

# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------

settings.register_profile(
    "ci",
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "dev",
    max_examples=50,
)

# Ensure project root is on sys.path for `import *`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def mock_console_logger() -> MagicMock:
    """Mock console logger for testing."""
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_error_logger() -> MagicMock:
    """Mock error logger for testing."""
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def console_logger(request: pytest.FixtureRequest) -> logging.Logger:
    """Auto-named console logger from test module."""
    module_name = request.module.__name__.split(".")[-1]
    return logging.getLogger(f"test.{module_name}.console")


@pytest.fixture
def error_logger(request: pytest.FixtureRequest) -> logging.Logger:
    """Auto-named error logger from test module."""
    module_name = request.module.__name__.split(".")[-1]
    return logging.getLogger(f"test.{module_name}.error")


# ---------------------------------------------------------------------------
# AppConfig test factory: use tests.factories.create_test_app_config()
# ---------------------------------------------------------------------------
