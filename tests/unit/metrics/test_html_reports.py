"""Unit tests for html_reports module."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from metrics.html_reports import (
    DURATION_FIELD,
    determine_event_row_class,
    format_event_table_row,
    generate_empty_html_template,
    generate_grouped_success_table,
    generate_summary_table_html,
    get_duration_category,
    group_events_by_duration_and_success,
    save_html_report,
)
from tests.factories import create_test_app_config


def _create_simple_event(
    duration: float,
    *,
    success: bool = True,
) -> dict[str, Any]:
    """Create a simple event with duration and success status."""
    return {DURATION_FIELD: duration, "Success": success}


@pytest.fixture
def sample_events() -> list[dict[str, Any]]:
    """Create sample analytics events."""
    return [
        {
            "Function": "process_track",
            "Event Type": "execution",
            "Start Time": "10:00:00",
            "End Time": "10:00:01",
            DURATION_FIELD: 1.0,
            "Success": True,
        },
        {
            "Function": "fetch_data",
            "Event Type": "api_call",
            "Start Time": "10:00:02",
            "End Time": "10:00:05",
            DURATION_FIELD: 3.0,
            "Success": True,
        },
        {
            "Function": "save_result",
            "Event Type": "io",
            "Start Time": "10:00:06",
            "End Time": "10:00:16",
            DURATION_FIELD: 10.0,
            "Success": False,
        },
    ]


@pytest.fixture
def duration_thresholds() -> dict[str, float]:
    """Create default duration thresholds."""
    return {"short_max": 2, "medium_max": 5, "long_max": 10}


class TestDurationField:
    """Tests for DURATION_FIELD constant."""

    def test_duration_field_value(self) -> None:
        """Should have correct value."""
        assert DURATION_FIELD == "Duration (s)"


class TestGetDurationCategory:
    """Tests for get_duration_category function."""

    def test_short_duration(self, duration_thresholds: dict[str, float]) -> None:
        """Should return 'short' for durations <= short_max."""
        assert get_duration_category(1.0, duration_thresholds) == "short"
        assert get_duration_category(2.0, duration_thresholds) == "short"

    def test_medium_duration(self, duration_thresholds: dict[str, float]) -> None:
        """Should return 'medium' for durations between short_max and medium_max."""
        assert get_duration_category(3.0, duration_thresholds) == "medium"
        assert get_duration_category(5.0, duration_thresholds) == "medium"

    def test_long_duration(self, duration_thresholds: dict[str, float]) -> None:
        """Should return 'long' for durations > medium_max."""
        assert get_duration_category(6.0, duration_thresholds) == "long"
        assert get_duration_category(100.0, duration_thresholds) == "long"

    def test_uses_default_thresholds(self) -> None:
        """Should use default thresholds when not provided."""
        result = get_duration_category(1.5, {})
        assert result == "short"


class TestDetermineEventRowClass:
    """Tests for determine_event_row_class function."""

    def test_returns_error_for_failed_event(self, duration_thresholds: dict[str, float]) -> None:
        """Should return 'error' class for failed events."""
        event = _create_simple_event(1.0, success=False)
        result = determine_event_row_class(event, duration_thresholds)
        assert result == "error"

    @pytest.mark.parametrize(
        ("duration", "expected_class"),
        [
            (1.0, "duration-short"),
            (4.0, "duration-medium"),
            (10.0, "duration-long"),
        ],
    )
    def test_returns_correct_class_for_successful_event(
        self,
        duration_thresholds: dict[str, float],
        duration: float,
        expected_class: str,
    ) -> None:
        """Should return correct class based on duration for successful events."""
        event = _create_simple_event(duration)
        result = determine_event_row_class(event, duration_thresholds)
        assert result == expected_class

    def test_handles_missing_success_key(self, duration_thresholds: dict[str, float]) -> None:
        """Should default to error when Success key is missing."""
        event: dict[str, Any] = {DURATION_FIELD: 1.0}
        result = determine_event_row_class(event, duration_thresholds)
        assert result == "error"


class TestFormatEventTableRow:
    """Tests for format_event_table_row function."""

    def test_formats_complete_event(self) -> None:
        """Should format event with all fields."""
        event = {
            "Function": "test_func",
            "Event Type": "execution",
            "Start Time": "10:00:00",
            "End Time": "10:00:05",
            DURATION_FIELD: 5.0,
            "Success": True,
        }
        result = format_event_table_row(event, "duration-medium")

        assert 'class="duration-medium"' in result
        assert "<td>test_func</td>" in result
        assert "<td>execution</td>" in result
        assert "<td>10:00:00</td>" in result
        assert "<td>10:00:05</td>" in result
        assert "<td>5.0</td>" in result
        assert "<td>Yes</td>" in result

    def test_formats_failed_event(self) -> None:
        """Should show 'No' for failed events."""
        event = {
            "Function": "test_func",
            "Event Type": "execution",
            DURATION_FIELD: 1.0,
            "Success": False,
        }
        result = format_event_table_row(event, "error")

        assert 'class="error"' in result
        assert "<td>No</td>" in result

    def test_handles_missing_fields(self) -> None:
        """Should use 'Unknown' for missing fields."""
        event: dict[str, Any] = {DURATION_FIELD: 1.0, "Success": True}
        result = format_event_table_row(event, "duration-short")

        assert "<td>Unknown</td>" in result


class TestGroupEventsByDurationAndSuccess:
    """Tests for group_events_by_duration_and_success function."""

    def test_returns_all_events_when_grouping_disabled(
        self,
        sample_events: list[dict[str, Any]],
        duration_thresholds: dict[str, float],
        error_logger: logging.Logger,
    ) -> None:
        """Should return all events when grouping is disabled."""
        grouped, remaining = group_events_by_duration_and_success(
            sample_events,
            duration_thresholds,
            group_successful_short_calls=False,
            error_logger=error_logger,
        )

        assert grouped == {}
        assert remaining == sample_events

    def test_groups_short_successful_events(
        self,
        duration_thresholds: dict[str, float],
        error_logger: logging.Logger,
    ) -> None:
        """Should group short successful events by function and type."""
        events = [
            {
                "Function": "func_a",
                "Event Type": "type_1",
                DURATION_FIELD: 1.0,
                "Success": True,
            },
            {
                "Function": "func_a",
                "Event Type": "type_1",
                DURATION_FIELD: 0.5,
                "Success": True,
            },
        ]

        grouped, remaining = group_events_by_duration_and_success(
            events,
            duration_thresholds,
            group_successful_short_calls=True,
            error_logger=error_logger,
        )

        assert ("func_a", "type_1") in grouped
        assert grouped[("func_a", "type_1")]["count"] == 2
        assert grouped[("func_a", "type_1")]["total_duration"] == 1.5
        assert remaining == []

    def test_separates_long_and_failed_events(
        self,
        sample_events: list[dict[str, Any]],
        duration_thresholds: dict[str, float],
        error_logger: logging.Logger,
    ) -> None:
        """Should keep long and failed events in remaining list."""
        _grouped, remaining = group_events_by_duration_and_success(
            sample_events,
            duration_thresholds,
            group_successful_short_calls=True,
            error_logger=error_logger,
        )

        assert len(remaining) == 2
        assert any(e[DURATION_FIELD] == 3.0 for e in remaining)
        assert any(e["Success"] is False for e in remaining)

    def test_handles_invalid_duration_type(
        self,
        duration_thresholds: dict[str, float],
        error_logger: logging.Logger,
    ) -> None:
        """Should handle events with invalid duration type."""
        events = [
            {
                "Function": "func",
                "Event Type": "type",
                DURATION_FIELD: "invalid",
                "Success": True,
            }
        ]

        grouped, remaining = group_events_by_duration_and_success(
            events,
            duration_thresholds,
            group_successful_short_calls=True,
            error_logger=error_logger,
        )

        assert grouped == {}
        assert len(remaining) == 1

    def test_handles_missing_keys(
        self,
        duration_thresholds: dict[str, float],
        error_logger: logging.Logger,
    ) -> None:
        """Should handle events with missing required keys."""
        events: list[dict[str, Any]] = [{"Function": "func"}]

        _grouped, remaining = group_events_by_duration_and_success(
            events,
            duration_thresholds,
            group_successful_short_calls=True,
            error_logger=error_logger,
        )

        assert len(remaining) == 1


class TestGenerateGroupedSuccessTable:
    """Tests for generate_grouped_success_table function."""

    def test_shows_disabled_message_when_disabled(self) -> None:
        """Should show message when grouping disabled."""
        result = generate_grouped_success_table({}, group_successful_short_calls=False)
        assert "No short successful calls found or grouping disabled" in result

    def test_shows_message_when_no_grouped_data(self) -> None:
        """Should show message when no grouped events."""
        result = generate_grouped_success_table({}, group_successful_short_calls=True)
        assert "No short successful calls found or grouping disabled" in result

    def test_generates_table_with_grouped_data(self) -> None:
        """Should generate table with grouped events."""
        grouped = {
            ("func_a", "type_1"): {"count": 5, "total_duration": 2.5},
            ("func_b", "type_2"): {"count": 3, "total_duration": 1.2},
        }

        result = generate_grouped_success_table(grouped, group_successful_short_calls=True)

        assert "<h3>Grouped Short & Successful Calls</h3>" in result
        assert "<td>func_a</td>" in result
        assert "<td>type_1</td>" in result
        assert "<td>5</td>" in result
        assert "<td>0.5</td>" in result


class TestGenerateSummaryTableHtml:
    """Tests for generate_summary_table_html function."""

    def test_generates_summary_with_data(self) -> None:
        """Should generate summary table with call data."""
        call_counts = {"func_a": 10, "func_b": 5}
        success_counts = {"func_a": 9, "func_b": 5}
        decorator_overhead = {"func_a": 0.01, "func_b": 0.005}

        result = generate_summary_table_html(call_counts, success_counts, decorator_overhead)

        assert "<h3>Summary</h3>" in result
        assert "<td>func_a</td>" in result
        assert "<td>10</td>" in result
        assert "<td>9</td>" in result
        assert "90.00" in result

    def test_generates_empty_message_when_no_data(self) -> None:
        """Should show 'no function calls' message when empty."""
        result = generate_summary_table_html({}, {}, {})

        assert "No function calls recorded" in result

    def test_handles_zero_call_count(self) -> None:
        """Should handle zero call count without division error."""
        call_counts = {"func_a": 0}
        success_counts = {"func_a": 0}
        decorator_overhead = {"func_a": 0.0}

        result = generate_summary_table_html(call_counts, success_counts, decorator_overhead)

        assert "0.00" in result


class TestGenerateEmptyHtmlTemplate:
    """Tests for generate_empty_html_template function."""

    def test_creates_empty_template_file(
        self,
        tmp_path: Path,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should create empty HTML template file."""
        report_file = tmp_path / "empty_report.html"

        generate_empty_html_template("2024-01-15", str(report_file), console_logger, error_logger)

        assert report_file.exists()
        content = report_file.read_text()
        assert "Analytics Report for 2024-01-15" in content
        assert "No analytics data was collected" in content

    def test_creates_parent_directories(
        self,
        tmp_path: Path,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should create parent directories if needed."""
        report_file = tmp_path / "nested" / "dir" / "report.html"

        generate_empty_html_template("2024-01-15", str(report_file), console_logger, error_logger)

        assert report_file.exists()

    def test_handles_write_error(
        self,
        tmp_path: Path,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should handle write errors gracefully."""
        report_file = tmp_path / "report.html"

        with patch("metrics.html_reports.Path.open", side_effect=OSError("Write error")):
            generate_empty_html_template("2024-01-15", str(report_file), console_logger, error_logger)


class TestSaveHtmlReport:
    """Tests for save_html_report function."""

    def test_creates_report_with_events(
        self,
        tmp_path: Path,
        sample_events: list[dict[str, Any]],
    ) -> None:
        """Should create HTML report with events."""
        config = create_test_app_config(logs_base_dir=str(tmp_path))
        call_counts = {"process_track": 1, "fetch_data": 1, "save_result": 1}
        success_counts = {"process_track": 1, "fetch_data": 1, "save_result": 0}
        decorator_overhead = {"process_track": 0.01}

        save_html_report(
            events=sample_events,
            call_counts=call_counts,
            success_counts=success_counts,
            decorator_overhead=decorator_overhead,
            config=config,
        )

        report_file = tmp_path / "analytics" / "analytics_incremental.html"
        assert report_file.exists()

    def test_creates_empty_template_when_no_data(self, tmp_path: Path) -> None:
        """Should create empty template when no events or counts."""
        config = create_test_app_config(logs_base_dir=str(tmp_path))

        save_html_report(
            events=[],
            call_counts={},
            success_counts={},
            decorator_overhead={},
            config=config,
        )

        report_file = tmp_path / "analytics" / "analytics_incremental.html"
        assert report_file.exists()
        content = report_file.read_text()
        assert "No analytics data was collected" in content

    def test_uses_force_mode_filename(
        self,
        tmp_path: Path,
        sample_events: list[dict[str, Any]],
    ) -> None:
        """Should use full report filename in force mode."""
        config = create_test_app_config(logs_base_dir=str(tmp_path))

        save_html_report(
            events=sample_events,
            call_counts={"func": 1},
            success_counts={"func": 1},
            decorator_overhead={},
            config=config,
            force_mode=True,
        )

        report_file = tmp_path / "analytics" / "analytics_full.html"
        assert report_file.exists()

    def test_groups_short_successful_calls(
        self,
        tmp_path: Path,
    ) -> None:
        """Should group short successful calls when enabled."""
        config = create_test_app_config(logs_base_dir=str(tmp_path))
        events = [
            {
                "Function": "fast_func",
                "Event Type": "exec",
                DURATION_FIELD: 0.5,
                "Success": True,
            }
            for _ in range(5)
        ]

        save_html_report(
            events=events,
            call_counts={"fast_func": 5},
            success_counts={"fast_func": 5},
            decorator_overhead={},
            config=config,
            group_successful_short_calls=True,
        )

        report_file = tmp_path / "analytics" / "analytics_incremental.html"
        content = report_file.read_text()
        assert "Grouped Short & Successful Calls" in content

    def test_uses_default_loggers_when_not_provided(
        self,
        tmp_path: Path,
        sample_events: list[dict[str, Any]],
    ) -> None:
        """Should use default loggers when not provided."""
        config = create_test_app_config(logs_base_dir=str(tmp_path))

        save_html_report(
            events=sample_events,
            call_counts={"func": 1},
            success_counts={"func": 1},
            decorator_overhead={},
            config=config,
        )

        report_file = tmp_path / "analytics" / "analytics_incremental.html"
        assert report_file.exists(), "save_html_report should create report file with default loggers"

    def test_handles_save_error(
        self,
        tmp_path: Path,
        sample_events: list[dict[str, Any]],
        error_logger: logging.Logger,
    ) -> None:
        """Should handle file save errors."""
        config = create_test_app_config(logs_base_dir=str(tmp_path))

        with patch("metrics.html_reports.Path.open", side_effect=OSError("Write error")):
            save_html_report(
                events=sample_events,
                call_counts={"func": 1},
                success_counts={"func": 1},
                decorator_overhead={},
                config=config,
                error_logger=error_logger,
            )
