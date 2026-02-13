"""Comprehensive tests for BaseProcessor (#219)."""

from __future__ import annotations

import logging
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from core.models.protocols import AnalyticsProtocol
from core.tracks.track_base import BaseProcessor
from tests.factories import create_test_app_config  # sourcery skip: dont-import-test-modules


@pytest.fixture
def processor() -> BaseProcessor:
    """Create BaseProcessor with dry_run=True."""
    return BaseProcessor(
        console_logger=logging.getLogger("test.base.console"),
        error_logger=logging.getLogger("test.base.error"),
        analytics=cast(AnalyticsProtocol, cast(object, MagicMock())),
        config=create_test_app_config(),
        dry_run=True,
    )


@pytest.fixture
def live_processor() -> BaseProcessor:
    """Create BaseProcessor with dry_run=False."""
    return BaseProcessor(
        console_logger=logging.getLogger("test.base.console"),
        error_logger=logging.getLogger("test.base.error"),
        analytics=cast(AnalyticsProtocol, cast(object, MagicMock())),
        config=create_test_app_config(),
        dry_run=False,
    )


class TestBaseProcessorInit:
    def test_stores_all_dependencies(self, processor: BaseProcessor) -> None:
        assert processor.dry_run is True
        assert isinstance(processor.console_logger, logging.Logger)
        assert isinstance(processor.error_logger, logging.Logger)
        assert processor.config is not None

    def test_starts_with_empty_actions(self, processor: BaseProcessor) -> None:
        assert processor.get_dry_run_actions() == []


class TestDryRunRecording:
    def test_records_action_in_dry_run_mode(self, processor: BaseProcessor) -> None:
        details: dict[str, Any] = {"track_id": "42", "year": 2020}
        processor._record_dry_run_action("year_update", details)

        actions = processor.get_dry_run_actions()
        assert len(actions) == 1
        assert actions[0]["type"] == "year_update"
        assert actions[0]["details"]["track_id"] == "42"

    def test_skips_recording_when_not_dry_run(self, live_processor: BaseProcessor) -> None:
        live_processor._record_dry_run_action("year_update", {"id": "1"})
        assert live_processor.get_dry_run_actions() == []

    def test_multiple_actions_accumulate(self, processor: BaseProcessor) -> None:
        processor._record_dry_run_action("update", {"id": "1"})
        processor._record_dry_run_action("skip", {"id": "2"})
        processor._record_dry_run_action("update", {"id": "3"})
        assert len(processor.get_dry_run_actions()) == 3


class TestGetDryRunActions:
    def test_returns_copy_not_reference(self, processor: BaseProcessor) -> None:
        processor._record_dry_run_action("test", {"key": "value"})
        actions = processor.get_dry_run_actions()

        # Mutating the returned list should not affect internal state
        actions.clear()
        assert len(processor.get_dry_run_actions()) == 1

    def test_empty_when_no_actions(self, processor: BaseProcessor) -> None:
        assert processor.get_dry_run_actions() == []


class TestClearDryRunActions:
    def test_clears_all_recorded_actions(self, processor: BaseProcessor) -> None:
        processor._record_dry_run_action("a", {})
        processor._record_dry_run_action("b", {})
        assert len(processor.get_dry_run_actions()) == 2

        processor.clear_dry_run_actions()
        assert processor.get_dry_run_actions() == []

    def test_clear_on_empty_is_noop(self, processor: BaseProcessor) -> None:
        processor.clear_dry_run_actions()
        assert processor.get_dry_run_actions() == []
