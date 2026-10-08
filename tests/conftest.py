"""Pytest configuration and shared fixtures for Genres Autoupdater v2.0.

This module configures the test environment by ensuring the project root
is added to sys.path, allowing imports of the src package modules.
"""

from __future__ import annotations

import io
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, settings
from rich.console import Console
from rich.logging import RichHandler

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
    return MagicMock()


@pytest.fixture
def mock_error_logger() -> MagicMock:
    """Mock error logger for testing."""
    return MagicMock()


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


@pytest.fixture
def rich_console_logger(request: pytest.FixtureRequest) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """Console logger rendered by Rich with markup on, as the app's console is, and the text it printed."""
    output = io.StringIO()
    # No color system, so FORCE_COLOR in the environment cannot split the printed text with escape codes
    handler = RichHandler(console=Console(file=output, width=500, color_system=None), markup=True, show_path=False)
    logger = logging.getLogger(f"test.{request.node.name}.rich_console")
    logger.addHandler(handler)
    logger.propagate = False
    yield logger, output
    logger.removeHandler(handler)
    logger.propagate = True


# ---------------------------------------------------------------------------
# AppConfig test factory: use tests.factories.create_test_app_config()
# ---------------------------------------------------------------------------
