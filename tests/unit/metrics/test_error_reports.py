"""Tests for src/metrics/error_reports.py."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from src.metrics.error_reports import (
    ErrorCategory,
    ErrorClassifier,
    ErrorEvent,
    ErrorMetricsCollector,
    ErrorPattern,
    ErrorPatternDetector,
    ErrorRateTracker,
    ErrorRecordingRequest,
    ErrorSeverity,
    ErrorStats,
    default_error_alert_handler,
)


class TestErrorSeverity:
    """Tests for ErrorSeverity enum."""

    def test_severity_values(self) -> None:
        """Verify all severity values."""
        assert ErrorSeverity.LOW.value == "low"
        assert ErrorSeverity.MEDIUM.value == "medium"
        assert ErrorSeverity.HIGH.value == "high"
        assert ErrorSeverity.CRITICAL.value == "critical"

    def test_severity_count(self) -> None:
        """Verify correct number of severity levels."""
        assert len(ErrorSeverity) == 4


class TestErrorCategory:
    """Tests for ErrorCategory enum."""

    def test_category_values(self) -> None:
        """Verify key category values."""
        assert ErrorCategory.API_ERROR.value == "api_error"
        assert ErrorCategory.DATABASE_ERROR.value == "database_error"
        assert ErrorCategory.NETWORK_ERROR.value == "network_error"
        assert ErrorCategory.TIMEOUT_ERROR.value == "timeout_error"
        assert ErrorCategory.UNKNOWN_ERROR.value == "unknown_error"

    def test_category_count(self) -> None:
        """Verify correct number of categories."""
        assert len(ErrorCategory) == 10


class TestErrorEvent:
    """Tests for ErrorEvent dataclass."""

    def test_error_event_creation(self) -> None:
        """Test ErrorEvent can be created with required fields."""
        event = ErrorEvent(
            timestamp=datetime.now(UTC),
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            message="Test error",
            exception_type="ValueError",
        )
        assert event.message == "Test error"
        assert event.category == ErrorCategory.API_ERROR
        assert event.severity == ErrorSeverity.HIGH

    def test_error_event_signature_generated(self) -> None:
        """Test signature is automatically generated."""
        event = ErrorEvent(
            timestamp=datetime.now(UTC),
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            message="Test error",
            exception_type="ValueError",
        )
        assert hasattr(event, "signature")
        assert len(event.signature) == 16

    def test_error_event_signature_normalizes_numbers(self) -> None:
        """Test signature normalizes numbers in message."""
        event1 = ErrorEvent(
            timestamp=datetime.now(UTC),
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            message="Error at line 123",
            exception_type="ValueError",
        )
        event2 = ErrorEvent(
            timestamp=datetime.now(UTC),
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            message="Error at line 456",
            exception_type="ValueError",
        )
        # Signatures should be the same (numbers normalized)
        assert event1.signature == event2.signature

    def test_error_event_optional_fields(self) -> None:
        """Test optional fields have correct defaults."""
        event = ErrorEvent(
            timestamp=datetime.now(UTC),
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            message="Test error",
            exception_type="ValueError",
        )
        assert event.stack_trace is None
        assert event.context == {}
        assert event.source_module is None
        assert event.error_code is None
        assert event.user_id is None


class TestErrorPattern:
    """Tests for ErrorPattern dataclass."""

    def test_error_pattern_creation(self) -> None:
        """Test ErrorPattern can be created."""
        now = datetime.now(UTC)
        pattern = ErrorPattern(
            signature="abc123",
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            count=5,
            first_seen=now - timedelta(hours=1),
            last_seen=now,
            sample_message="Test error",
        )
        assert pattern.signature == "abc123"
        assert pattern.count == 5
        assert pattern.trend == "stable"  # default
        assert pattern.impact_score == 0.0  # default


class TestErrorRecordingRequest:
    """Tests for ErrorRecordingRequest dataclass."""

    def test_request_with_exception(self) -> None:
        """Test request with exception."""
        exc = ValueError("Test error")
        request = ErrorRecordingRequest(exception=exc)
        assert request.exception is exc
        assert request.message is None

    def test_request_with_message(self) -> None:
        """Test request with message only."""
        request = ErrorRecordingRequest(message="Custom message")
        assert request.exception is None
        assert request.message == "Custom message"

    def test_request_all_fields(self) -> None:
        """Test request with all fields."""
        request = ErrorRecordingRequest(
            exception=ValueError("Test"),
            message="Override message",
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.LOW,
            context={"key": "value"},
            source_module="test_module",
            error_code="E001",
            user_id="user123",
        )
        assert request.category == ErrorCategory.VALIDATION_ERROR
        assert request.context == {"key": "value"}


class TestErrorStats:
    """Tests for ErrorStats dataclass."""

    def test_error_stats_defaults(self) -> None:
        """Test ErrorStats has correct defaults."""
        stats = ErrorStats()
        assert stats.total_errors == 0
        assert stats.error_rate_per_minute == 0.0
        assert stats.unique_errors == 0
        assert stats.top_patterns == []

    def test_error_stats_with_values(self) -> None:
        """Test ErrorStats with values."""
        stats = ErrorStats(
            total_errors=10,
            error_rate_per_minute=2.5,
            unique_errors=3,
        )
        assert stats.total_errors == 10
        assert stats.error_rate_per_minute == 2.5


class TestErrorClassifier:
    """Tests for ErrorClassifier class."""

    def test_classify_api_error(self) -> None:
        """Test classification of API errors."""
        category, severity = ErrorClassifier.classify_error(
            "HTTPError",
            "API request failed with status 500",
        )
        assert category == ErrorCategory.API_ERROR
        assert severity == ErrorSeverity.MEDIUM

    def test_classify_timeout_error(self) -> None:
        """Test classification of timeout errors."""
        category, severity = ErrorClassifier.classify_error(
            "TimeoutError",
            "Connection timed out",
        )
        assert category == ErrorCategory.TIMEOUT_ERROR
        assert severity == ErrorSeverity.HIGH

    def test_classify_network_error(self) -> None:
        """Test classification of network errors."""
        category, severity = ErrorClassifier.classify_error(
            "ConnectionError",
            "Connection refused",
        )
        assert category == ErrorCategory.NETWORK_ERROR
        assert severity == ErrorSeverity.HIGH

    def test_classify_database_error(self) -> None:
        """Test classification of database errors."""
        category, severity = ErrorClassifier.classify_error(
            "DatabaseError",
            "SQL query failed",
        )
        assert category == ErrorCategory.DATABASE_ERROR
        assert severity == ErrorSeverity.HIGH

    def test_classify_database_critical(self) -> None:
        """Test classification of critical database errors (deadlock)."""
        category, severity = ErrorClassifier.classify_error(
            "DeadlockError",
            "Deadlock in transaction",
        )
        assert category == ErrorCategory.DATABASE_ERROR
        assert severity == ErrorSeverity.CRITICAL

    def test_classify_auth_error(self) -> None:
        """Test classification of authentication errors."""
        category, severity = ErrorClassifier.classify_error(
            "AuthError",
            "Invalid token provided",
        )
        assert category == ErrorCategory.AUTHENTICATION_ERROR
        assert severity == ErrorSeverity.MEDIUM

    def test_classify_validation_error(self) -> None:
        """Test classification of validation errors."""
        category, severity = ErrorClassifier.classify_error(
            "ValidationError",
            "Invalid input format",
        )
        assert category == ErrorCategory.VALIDATION_ERROR
        assert severity == ErrorSeverity.LOW

    def test_classify_system_critical(self) -> None:
        """Test classification of critical system errors."""
        category, severity = ErrorClassifier.classify_error(
            "MemoryError",
            "Out of memory",
        )
        assert category == ErrorCategory.SYSTEM_ERROR
        assert severity == ErrorSeverity.CRITICAL

    def test_classify_unknown_error(self) -> None:
        """Test classification of unknown errors."""
        category, severity = ErrorClassifier.classify_error(
            "CustomError",
            "Something happened",
        )
        assert category == ErrorCategory.UNKNOWN_ERROR
        assert severity == ErrorSeverity.MEDIUM

    def test_classify_with_stack_trace(self) -> None:
        """Test classification uses stack trace in analysis."""
        category, severity = ErrorClassifier.classify_error(
            "Exception",
            "Unknown error",
            "File database.py, line 100\nDatabaseError: query failed",
        )
        # Should detect database from stack trace
        assert category == ErrorCategory.DATABASE_ERROR
        assert severity == ErrorSeverity.HIGH


class TestErrorRateTracker:
    """Tests for ErrorRateTracker class."""

    def test_initial_state(self) -> None:
        """Test initial state of tracker."""
        tracker = ErrorRateTracker()
        assert tracker.total_errors == 0
        assert tracker.get_error_rate() == 0.0

    def test_record_error_increments_count(self) -> None:
        """Test recording errors increments count."""
        tracker = ErrorRateTracker()
        tracker.record_error()
        tracker.record_error()
        assert tracker.total_errors == 2

    def test_get_error_rate(self) -> None:
        """Test error rate calculation."""
        tracker = ErrorRateTracker(window_minutes=1, bucket_size_seconds=60)
        tracker.record_error()
        tracker.record_error()
        tracker.record_error()
        rate = tracker.get_error_rate()
        assert rate == 3.0  # 3 errors per 1 minute window

    def test_get_trend_stable(self) -> None:
        """Test stable trend detection."""
        tracker = ErrorRateTracker(bucket_size_seconds=1)
        # Need at least MIN_BUCKETS_FOR_TREND_ANALYSIS
        for _ in range(4):
            tracker.error_buckets.append(1)
        trend = tracker.get_trend()
        assert trend == "stable"

    def test_get_trend_insufficient_data(self) -> None:
        """Test trend returns stable with insufficient data."""
        tracker = ErrorRateTracker()
        # Clear buckets to simulate insufficient data
        tracker.error_buckets.clear()
        for _ in range(2):
            tracker.error_buckets.append(1)
        trend = tracker.get_trend()
        assert trend == "stable"


class TestErrorPatternDetector:
    """Tests for ErrorPatternDetector class."""

    def test_initial_state(self) -> None:
        """Test initial state of detector."""
        detector = ErrorPatternDetector()
        assert len(detector.detected_patterns) == 0
        assert len(detector.pattern_counts) == 0

    def test_record_error_below_threshold(self) -> None:
        """Test errors below threshold don't create pattern."""
        detector = ErrorPatternDetector(min_occurrences=3)
        event = ErrorEvent(
            timestamp=datetime.now(UTC),
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            message="Test error",
            exception_type="ValueError",
        )
        detector.record_error(event)
        detector.record_error(event)
        # Only 2 occurrences, threshold is 3
        assert len(detector.detected_patterns) == 0

    def test_record_error_creates_pattern(self) -> None:
        """Test pattern is created at threshold."""
        detector = ErrorPatternDetector(min_occurrences=3)
        for _ in range(3):
            event = ErrorEvent(
                timestamp=datetime.now(UTC),
                category=ErrorCategory.API_ERROR,
                severity=ErrorSeverity.HIGH,
                message="Test error",
                exception_type="ValueError",
            )
            detector.record_error(event)
        assert len(detector.detected_patterns) == 1

    def test_get_active_patterns_empty(self) -> None:
        """Test get_active_patterns with no patterns."""
        detector = ErrorPatternDetector()
        patterns = detector.get_active_patterns()
        assert patterns == []

    def test_get_active_patterns_sorted_by_impact(self) -> None:
        """Test patterns are sorted by impact score."""
        detector = ErrorPatternDetector(min_occurrences=2)

        # Create high impact pattern
        for _ in range(5):
            event = ErrorEvent(
                timestamp=datetime.now(UTC),
                category=ErrorCategory.API_ERROR,
                severity=ErrorSeverity.CRITICAL,
                message="Critical error",
                exception_type="CriticalError",
            )
            detector.record_error(event)

        # Create lower impact pattern
        for _ in range(2):
            event = ErrorEvent(
                timestamp=datetime.now(UTC),
                category=ErrorCategory.VALIDATION_ERROR,
                severity=ErrorSeverity.LOW,
                message="Validation error",
                exception_type="ValidationError",
            )
            detector.record_error(event)

        patterns = detector.get_active_patterns()
        # Higher impact should be first
        assert patterns[0].severity == ErrorSeverity.CRITICAL

    def test_pattern_removes_old_timestamps(self) -> None:
        """Test old timestamps are removed from pattern."""
        detector = ErrorPatternDetector(time_window_minutes=1)

        # Record event with old timestamp
        old_event = ErrorEvent(
            timestamp=datetime.now(UTC) - timedelta(hours=2),
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            message="Old error",
            exception_type="ValueError",
        )
        detector.record_error(old_event)

        # The old timestamp should be cleaned up
        # Pattern shouldn't form from stale data


class TestErrorMetricsCollector:
    """Tests for ErrorMetricsCollector class."""

    def test_initial_state(self) -> None:
        """Test initial state of collector."""
        collector = ErrorMetricsCollector()
        assert len(collector.all_errors) == 0
        assert len(collector.alert_handlers) == 0

    def test_record_error_with_exception(self) -> None:
        """Test recording error from exception."""
        collector = ErrorMetricsCollector()
        exc = ValueError("Test error")
        event = collector.record_error(exception=exc)
        assert event.exception_type == "ValueError"
        assert "Test error" in event.message
        assert len(collector.all_errors) == 1

    def test_record_error_with_message(self) -> None:
        """Test recording error from message."""
        collector = ErrorMetricsCollector()
        event = collector.record_error(message="Custom error message")
        assert "Custom error message" in event.message

    def test_record_error_with_request(self) -> None:
        """Test recording error with ErrorRecordingRequest."""
        collector = ErrorMetricsCollector()
        request = ErrorRecordingRequest(
            exception=ValueError("Test"),
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.LOW,
        )
        event = collector.record_error(request=request)
        assert event.category == ErrorCategory.VALIDATION_ERROR
        assert event.severity == ErrorSeverity.LOW

    def test_record_error_auto_classifies(self) -> None:
        """Test error is auto-classified if not specified."""
        collector = ErrorMetricsCollector()
        exc = TimeoutError("Connection timed out")
        event = collector.record_error(exception=exc)
        assert event.category == ErrorCategory.TIMEOUT_ERROR
        assert event.severity == ErrorSeverity.HIGH

    def test_add_alert_handler(self) -> None:
        """Test adding alert handler."""
        collector = ErrorMetricsCollector()
        handler = MagicMock()
        collector.add_alert_handler(handler)
        assert handler in collector.alert_handlers

    def test_alert_triggered_on_critical_error(self) -> None:
        """Test alert is triggered for critical errors."""
        collector = ErrorMetricsCollector()
        handler = MagicMock()
        collector.add_alert_handler(handler)

        request = ErrorRecordingRequest(
            exception=ValueError("Critical failure"),
            severity=ErrorSeverity.CRITICAL,
        )
        collector.record_error(request=request)

        handler.assert_called()
        call_args = handler.call_args[0]
        assert "Critical" in call_args[0]

    def test_alert_handler_exception_caught(self) -> None:
        """Test exceptions in alert handlers are caught."""
        collector = ErrorMetricsCollector()

        def failing_handler(
            _alert_type: str,
            _severity: ErrorSeverity,
            _data: dict[str, Any],
        ) -> None:
            """Test handler that raises."""
            raise RuntimeError("Handler failed")

        collector.add_alert_handler(failing_handler)

        # Should not raise
        request = ErrorRecordingRequest(
            exception=ValueError("Test"),
            severity=ErrorSeverity.CRITICAL,
        )
        collector.record_error(request=request)

    def test_get_error_stats(self) -> None:
        """Test getting error statistics."""
        collector = ErrorMetricsCollector()

        for _ in range(5):
            collector.record_error(exception=ValueError("Test"))

        stats = collector.get_error_stats()
        assert stats.total_errors == 5
        assert stats.unique_errors >= 1

    def test_get_error_stats_by_category(self) -> None:
        """Test statistics grouped by category."""
        collector = ErrorMetricsCollector()

        request = ErrorRecordingRequest(
            message="API error",
            category=ErrorCategory.API_ERROR,
        )
        collector.record_error(request=request)
        collector.record_error(request=request)

        request = ErrorRecordingRequest(
            message="DB error",
            category=ErrorCategory.DATABASE_ERROR,
        )
        collector.record_error(request=request)

        stats = collector.get_error_stats()
        assert stats.errors_by_category[ErrorCategory.API_ERROR] == 2
        assert stats.errors_by_category[ErrorCategory.DATABASE_ERROR] == 1

    def test_get_summary(self) -> None:
        """Test getting summary."""
        collector = ErrorMetricsCollector()
        collector.record_error(exception=ValueError("Test"))

        summary = collector.get_summary()
        assert "timestamp" in summary
        assert "total_errors" in summary
        assert "error_rate_per_minute" in summary
        assert "trend" in summary
        assert summary["total_errors"] == 1

    def test_prune_old_errors(self) -> None:
        """Test old errors are pruned."""
        collector = ErrorMetricsCollector(window_minutes=1)

        # Force prune by setting last prune time to past
        collector._last_prune_time = time.time() - 400

        # Add an old error directly (bypassing record_error timestamp)
        old_event = ErrorEvent(
            timestamp=datetime.now(UTC) - timedelta(hours=5),
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.HIGH,
            message="Old error",
            exception_type="ValueError",
        )
        collector.all_errors.append(old_event)

        # Record new error to trigger prune
        collector.record_error(exception=ValueError("New error"))

        # Old error should be pruned (outside 2x window)
        assert len(collector.all_errors) == 1
        assert collector.all_errors[0].message == "New error"


class TestDefaultErrorAlertHandler:
    """Tests for default_error_alert_handler function."""

    def test_logs_low_severity(self) -> None:
        """Test low severity uses INFO level."""
        with patch("src.metrics.error_reports.logger") as mock_logger:
            default_error_alert_handler(
                "Test Alert",
                ErrorSeverity.LOW,
                {"error_rate": 1.0},
            )
            mock_logger.log.assert_called_once()
            # First arg is log level (logging.INFO = 20)
            assert mock_logger.log.call_args[0][0] == 20

    def test_logs_critical_severity(self) -> None:
        """Test critical severity uses CRITICAL level."""
        with patch("src.metrics.error_reports.logger") as mock_logger:
            default_error_alert_handler(
                "Critical Alert",
                ErrorSeverity.CRITICAL,
                {"error_rate": 10.0},
            )
            mock_logger.log.assert_called_once()
            # First arg is log level (logging.CRITICAL = 50)
            assert mock_logger.log.call_args[0][0] == 50

    def test_includes_error_rate_in_message(self) -> None:
        """Test error rate is included in log message."""
        with patch("src.metrics.error_reports.logger") as mock_logger:
            default_error_alert_handler(
                "Test Alert",
                ErrorSeverity.HIGH,
                {"error_rate": 5.5},
            )
            # Check that error_rate value is passed to log
            call_args = mock_logger.log.call_args[0]
            assert 5.5 in call_args


class TestIntegration:
    """Integration tests for error_reports module."""

    def test_full_error_tracking_workflow(self) -> None:
        """Test complete error tracking workflow."""
        collector = ErrorMetricsCollector(window_minutes=60)
        alerts_received: list[tuple[str, ErrorSeverity, dict[str, Any]]] = []

        def alert_handler(
            alert_type: str,
            severity: ErrorSeverity,
            data: dict[str, Any],
        ) -> None:
            """Test alert handler."""
            alerts_received.append((alert_type, severity, data))

        collector.add_alert_handler(alert_handler)

        # Simulate various errors
        collector.record_error(exception=ValueError("Validation failed"))
        collector.record_error(exception=TimeoutError("Connection timeout"))
        collector.record_error(
            request=ErrorRecordingRequest(
                exception=RuntimeError("Critical failure"),
                severity=ErrorSeverity.CRITICAL,
            )
        )

        # Check stats
        stats = collector.get_error_stats()
        assert stats.total_errors == 3
        assert stats.unique_errors >= 1

        # Check alerts were triggered
        assert len(alerts_received) > 0

    def test_pattern_detection_workflow(self) -> None:
        """Test pattern detection with repeated errors."""
        collector = ErrorMetricsCollector()

        # Generate repeated errors with same signature
        for _ in range(5):
            collector.record_error(
                request=ErrorRecordingRequest(
                    exception=ValueError("Same error message"),
                    category=ErrorCategory.VALIDATION_ERROR,
                )
            )

        summary = collector.get_summary()
        # Should detect pattern from repeated errors
        assert summary["unique_errors"] == 1
        assert summary["total_errors"] == 5
