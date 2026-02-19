"""Shared fixtures for integration tests."""

from __future__ import annotations

import pytest

from core.models.album_type import reset_patterns


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Reset album type patterns before each test.

    Prevents xdist global state pollution from tests that call
    configure_patterns() with custom patterns on the same worker.
    """
    _ = item
    reset_patterns()


def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Reset album type patterns after each test."""
    _ = item
    reset_patterns()
