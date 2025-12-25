"""Pytest configuration and shared fixtures for Genres Autoupdater v2.0.

This module configures the test environment by ensuring the project root
is added to sys.path, allowing imports of the src package modules.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is on sys.path for `import *`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def pytest_configure(config: pytest.Config) -> None:
    """Disable coverage when running under mutmut."""
    # Detect mutmut: check for mutants/ directory in Python path or PYTHONPATH
    pythonpath = os.environ.get("PYTHONPATH", "")
    is_mutmut = "mutants" in pythonpath or any("mutants" in str(p) for p in sys.path)

    if is_mutmut:
        # Unregister coverage plugin if already loaded
        if cov_plugin := config.pluginmanager.get_plugin("_cov"):
            config.pluginmanager.unregister(cov_plugin)

        # Block coverage plugins from loading
        config.pluginmanager.set_blocked("pytest_cov")
        config.pluginmanager.set_blocked("_cov")

        # Monkey-patch mutmut bug: record_trampoline_hit crashes when config is None
        # This happens in pytest-xdist workers that don't inherit mutmut.config
        # Bug location: mutmut/__main__.py:136-138 accesses config.max_stack_depth
        # without null check. Fixed in our fork until upstream fixes it.
        try:
            import mutmut
            import mutmut.__main__ as mutmut_main

            _original_record_trampoline_hit = mutmut_main.record_trampoline_hit

            def _patched_record_trampoline_hit(name: str) -> None:
                if mutmut.config is None:
                    return None  # Skip stats collection in xdist workers
                return _original_record_trampoline_hit(name)

            mutmut_main.record_trampoline_hit = _patched_record_trampoline_hit
        except (ImportError, AttributeError):
            pass  # mutmut not installed or API changed


@pytest.fixture
def mock_console_logger() -> MagicMock:
    """Mock console logger for testing."""
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_error_logger() -> MagicMock:
    """Mock error logger for testing."""
    return MagicMock(spec=logging.Logger)
