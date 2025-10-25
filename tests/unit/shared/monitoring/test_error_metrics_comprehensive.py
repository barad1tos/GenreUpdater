"""Comprehensive unit tests for error metrics module."""

from __future__ import annotations

import logging
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from src.shared.monitoring.error_metrics import (
    ErrorCategory,
    ErrorClassifier,
    ErrorEvent,
    ErrorMetricsCollector,
    ErrorPattern,
    ErrorPatternDetector,
    ErrorRateTracker,
    ErrorRecordingRequest,
    ErrorSeverity,
    default_error_alert_handler,
    get_error_collector,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


def _freeze_time(monkeypatch: pytest.MonkeyPatch, timeline: Iterable[float]) -> None:
    """Patch error_metrics time to yield deterministic timestamps."""
    iterator = iter(timeline)
    current_time = 0.0

    def fake_time() -> float:
        """Return next frozen timestamp from iterator."""
        nonlocal current_time
        with suppress(StopIteration):
            current_time = next(iterator)
        return current_time

    monkeypatch.setattr("src.shared.monitoring.error_metrics.time.time", fake_time)


def test_error_severity_values() -> None:
    """Ensure severity enum exposes expected values."""
    expected_values = {"low", "medium", "high", "critical"}
    assert {severity.value for severity in ErrorSeverity} == expected_values


def test_error_category_members() -> None:
    """Ensure category enum exposes expected values."""
    expected_categories = {
        "api_error",
        "database_error",
        "network_error",
        "authentication_error",
        "validation_error",
        "system_error",
        "user_error",
        "timeout_error",
        "permission_error",
        "unknown_error",
    }
    assert {category.value for category in ErrorCategory} == expected_categories


def test_error_event_signature_and_context() -> None:
    """Verify error event creates signature and preserves context."""
    timestamp = datetime.now(UTC)
    event = ErrorEvent(
        timestamp=timestamp,
        category=ErrorCategory.API_ERROR,
        severity=ErrorSeverity.HIGH,
        message="Service returned status 500",
        exception_type="HTTPError",
        context={"request_id": "abc123"},
    )

    assert len(event.signature) == 16
    assert event.context == {"request_id": "abc123"}

    no_context_event = ErrorEvent(
        timestamp=timestamp,
        category=ErrorCategory.API_ERROR,
        severity=ErrorSeverity.HIGH,
        message="Different payload failure",
        exception_type="HTTPError",
    )

    assert no_context_event.context == {}
    assert no_context_event.signature != event.signature


def test_error_pattern_fields() -> None:
    """Ensure pattern dataclass stores provided fields."""
    now = datetime.now(UTC)
    pattern = ErrorPattern(
        signature="abc123",
        category=ErrorCategory.NETWORK_ERROR,
        severity=ErrorSeverity.MEDIUM,
        count=5,
        first_seen=now - timedelta(minutes=10),
        last_seen=now,
        sample_message="Timeout while reaching service",
        trend="increasing",
        impact_score=2.5,
    )

    assert pattern.signature == "abc123"
    assert pattern.count == 5
    assert pattern.trend == "increasing"
    assert pattern.impact_score == pytest.approx(2.5)


@pytest.mark.parametrize(
    (
        "exception_type",
        "message",
        "expected_category",
        "expected_severity",
    ),
    [
        ("HTTPError", "API request failed with status 502", ErrorCategory.API_ERROR, ErrorSeverity.MEDIUM),
        ("TimeoutError", "Connection timed out", ErrorCategory.TIMEOUT_ERROR, ErrorSeverity.HIGH),
        ("ConnectionError", "Network connection refused", ErrorCategory.NETWORK_ERROR, ErrorSeverity.HIGH),
        ("ValueError", "Invalid payload format", ErrorCategory.VALIDATION_ERROR, ErrorSeverity.LOW),
        ("PermissionError", "Access denied by policy", ErrorCategory.PERMISSION_ERROR, ErrorSeverity.MEDIUM),
    ],
)
def test_error_classifier_rules(
    exception_type: str,
    message: str,
    expected_category: ErrorCategory,
    expected_severity: ErrorSeverity,
) -> None:
    """Validate classification rules map to expected categories and severity levels."""
    category, severity = ErrorClassifier.classify_error(exception_type, message)
    assert category == expected_category
    assert severity == expected_severity


def test_error_classifier_default_category() -> None:
    """Verify classifier falls back to unknown category."""
    category, severity = ErrorClassifier.classify_error("CustomError", "No matching rule")
    assert category == ErrorCategory.UNKNOWN_ERROR
    assert severity == ErrorSeverity.MEDIUM


def test_error_rate_tracker_records_and_trend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure rate tracker updates counts and computes trend."""
    _freeze_time(monkeypatch, [0.0, 0.0, 10.0])
    tracker = ErrorRateTracker(window_minutes=4)
    tracker.record_error()
    tracker.record_error()

    assert tracker.total_errors == 2
    assert tracker.get_error_rate() == pytest.approx(0.5, rel=0.1)

    tracker.error_buckets = deque([1, 1, 4, 5], maxlen=tracker.buckets_count)

    assert tracker.get_trend() == "spike"


def test_error_pattern_detector_identifies_repeated_events() -> None:
    """Check that repeated errors form an active pattern."""
    detector = ErrorPatternDetector(time_window_minutes=30)
    start = datetime.now(UTC) - timedelta(minutes=1)

    events = [
        ErrorEvent(
            timestamp=start + timedelta(seconds=offset * 10),
            category=ErrorCategory.NETWORK_ERROR,
            severity=ErrorSeverity.MEDIUM,
            message="Connection timeout 504",
            exception_type="TimeoutError",
        )
        for offset in range(3)
    ]

    for event in events:
        detector.record_error(event)

    active_patterns = detector.get_active_patterns()
    assert len(active_patterns) == 1
    pattern = active_patterns[0]
    assert pattern.count == 3
    assert pattern.category == ErrorCategory.NETWORK_ERROR


def test_error_pattern_detector_filters_by_impact() -> None:
    """Validate impact threshold filtering."""
    detector = ErrorPatternDetector(time_window_minutes=30)
    now = datetime.now(UTC)

    for minutes in range(3):
        detector.record_error(
            ErrorEvent(
                timestamp=now + timedelta(minutes=minutes),
                category=ErrorCategory.API_ERROR,
                severity=ErrorSeverity.HIGH,
                message="HTTP 500 from upstream",
                exception_type="HTTPError",
            )
        )

    active_patterns = detector.get_active_patterns(min_impact=1.0)
    assert active_patterns  # Expect at least one pattern above impact threshold


def test_error_metrics_collector_records_and_summarizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure collector stores events and produces summary."""
    _freeze_time(monkeypatch, [0.0, 0.0])
    collector = ErrorMetricsCollector(window_minutes=5)
    request = ErrorRecordingRequest(
        exception=ValueError("Invalid payload"),
        category=ErrorCategory.VALIDATION_ERROR,
        severity=ErrorSeverity.LOW,
        context={"field": "genre"},
    )

    event = collector.record_error(request=request)
    assert collector.all_errors[-1] is event

    stats = collector.get_error_stats(timedelta(minutes=5))
    assert stats.total_errors == 1
    assert stats.errors_by_category[ErrorCategory.VALIDATION_ERROR] == 1
    assert stats.errors_by_severity[ErrorSeverity.LOW] == 1

    summary = collector.get_summary()
    assert summary["total_errors"] == 1
    assert summary["errors_by_category"][ErrorCategory.VALIDATION_ERROR.value] == 1


def test_error_metrics_collector_triggers_custom_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure critical errors trigger registered alert handlers."""
    _freeze_time(monkeypatch, [0.0, 0.0])
    collector = ErrorMetricsCollector(window_minutes=1)
    captured_alerts: list[tuple[str, ErrorSeverity, dict[str, Any]]] = []

    def handler(alert_name: str, alert_severity: ErrorSeverity, payload: dict[str, Any]) -> None:
        """Capture alert invocations for later assertions."""
        captured_alerts.append((alert_name, alert_severity, payload))

    collector.add_alert_handler(handler)

    request = ErrorRecordingRequest(
        message="Critical system failure",
        category=ErrorCategory.SYSTEM_ERROR,
        severity=ErrorSeverity.CRITICAL,
    )
    collector.record_error(request=request)

    assert captured_alerts
    recorded_type, recorded_severity, recorded_payload = captured_alerts[-1]
    assert recorded_type == "Critical Error"
    assert recorded_severity == ErrorSeverity.CRITICAL
    assert "error_rate" in recorded_payload


def test_get_error_collector_singleton() -> None:
    """Verify get_error_collector returns the same instance."""
    collector = get_error_collector()
    collector.all_errors.clear()
    another_collector = get_error_collector()
    assert collector is another_collector


def test_default_error_alert_handler_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure default alert handler logs with correct severity."""
    with caplog.at_level(logging.CRITICAL):
        default_error_alert_handler(
            alert_type="Critical Error",
            severity=ErrorSeverity.CRITICAL,
            data={"error_rate": 12.0},
        )

    messages = [record.message for record in caplog.records]
    assert any("Critical Error" in message for message in messages)
