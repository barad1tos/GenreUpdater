"""Error metrics collection and analysis system.

This module provides error categorization, rate tracking, error pattern detection,
and alert generation for comprehensive error monitoring and analysis.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from re import Pattern
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Constants for error rate analysis
MIN_BUCKETS_FOR_TREND_ANALYSIS = 4
MIN_TIMESTAMPS_FOR_PATTERN_DETECTION = 6
HIGH_ERROR_RATE_THRESHOLD_PER_MINUTE = 10


class ErrorSeverity(Enum):
    """Error severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Error categories for classification."""

    API_ERROR = "api_error"
    DATABASE_ERROR = "database_error"
    NETWORK_ERROR = "network_error"
    AUTHENTICATION_ERROR = "authentication_error"
    VALIDATION_ERROR = "validation_error"
    SYSTEM_ERROR = "system_error"
    USER_ERROR = "user_error"
    TIMEOUT_ERROR = "timeout_error"
    PERMISSION_ERROR = "permission_error"
    UNKNOWN_ERROR = "unknown_error"


def _create_empty_context() -> dict[str, Any]:
    """Create an empty context dictionary with proper typing."""
    return {}


def _create_empty_pattern_list() -> list[ErrorPattern]:
    """Create empty pattern list with proper typing."""
    return []


@dataclass
class ErrorEvent:
    """Individual error event."""

    timestamp: datetime
    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    exception_type: str
    stack_trace: str | None = None
    context: dict[str, Any] = field(default_factory=_create_empty_context)
    source_module: str | None = None
    error_code: str | None = None
    user_id: str | None = None

    def __post_init__(self) -> None:
        """Generate error signature for pattern detection."""
        # Create a consistent error signature for pattern detection
        signature_parts = [
            self.exception_type,
            self.category.value,
            # Normalize message by removing variable parts
            re.sub(r"\d+", "N", self.message)[:100],
        ]

        signature_text = "|".join(signature_parts)
        self.signature = hashlib.md5(signature_text.encode(), usedforsecurity=False).hexdigest()[:16]


@dataclass
class ErrorPattern:
    """Detected error pattern."""

    signature: str
    category: ErrorCategory
    severity: ErrorSeverity
    count: int
    first_seen: datetime
    last_seen: datetime
    sample_message: str
    trend: str = "stable"  # increasing, decreasing, stable, spike
    impact_score: float = 0.0


@dataclass
class ErrorRecordingRequest:
    """Request parameters for recording an error."""

    exception: Exception | None = None
    message: str | None = None
    category: ErrorCategory | None = None
    severity: ErrorSeverity | None = None
    context: dict[str, Any] | None = None
    source_module: str | None = None
    error_code: str | None = None
    user_id: str | None = None


@dataclass
class ErrorStats:
    """Error statistics for a time period."""

    total_errors: int = 0
    errors_by_category: dict[ErrorCategory, int] = field(default_factory=lambda: defaultdict(lambda: 0))
    errors_by_severity: dict[ErrorSeverity, int] = field(default_factory=lambda: defaultdict(lambda: 0))
    error_rate_per_minute: float = 0.0
    unique_errors: int = 0
    top_patterns: list[ErrorPattern] = field(default_factory=_create_empty_pattern_list)
    time_window: timedelta = field(default_factory=lambda: timedelta(hours=1))


class ErrorClassifier:
    """Classifies errors into categories and severity levels."""

    # Error classification patterns
    CLASSIFICATION_RULES: ClassVar[dict[Pattern[str], tuple[ErrorCategory, ErrorSeverity]]] = {}

    @classmethod
    def _initialize_rules(cls) -> None:
        """Initialize classification rules."""
        if cls.CLASSIFICATION_RULES:
            return

        # API errors
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(api|http|status|response|request).*?(4\d{2}|5\d{2})")] = (
            ErrorCategory.API_ERROR,
            ErrorSeverity.MEDIUM,
        )
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(timeout|timed out|connection.*timeout)")] = (
            ErrorCategory.TIMEOUT_ERROR,
            ErrorSeverity.HIGH,
        )
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(connection.*refused|connection.*reset|network)")] = (
            ErrorCategory.NETWORK_ERROR,
            ErrorSeverity.HIGH,
        )

        # Database errors
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(database|db|sql|query|connection.*pool)")] = (
            ErrorCategory.DATABASE_ERROR,
            ErrorSeverity.HIGH,
        )
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(deadlock|lock.*timeout|constraint.*violation)")] = (
            ErrorCategory.DATABASE_ERROR,
            ErrorSeverity.CRITICAL,
        )

        # Authentication errors
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(auth|login|token|unauthorized|forbidden)")] = (
            ErrorCategory.AUTHENTICATION_ERROR,
            ErrorSeverity.MEDIUM,
        )
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(permission|access.*denied|not.*authorized)")] = (
            ErrorCategory.PERMISSION_ERROR,
            ErrorSeverity.MEDIUM,
        )

        # Validation errors
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(validation|invalid|malformed|bad.*format)")] = (
            ErrorCategory.VALIDATION_ERROR,
            ErrorSeverity.LOW,
        )

        # System errors
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(memory|disk|cpu|resource|out\s+of\s+memory)")] = (
            ErrorCategory.SYSTEM_ERROR,
            ErrorSeverity.CRITICAL,
        )
        cls.CLASSIFICATION_RULES[re.compile(r"(?i)(file\s*not\s*found|directory\s*not\s*found|path)")] = (
            ErrorCategory.SYSTEM_ERROR,
            ErrorSeverity.MEDIUM,
        )

    @classmethod
    def classify_error(
        cls,
        exception_type: str,
        message: str,
        stack_trace: str | None = None,
    ) -> tuple[ErrorCategory, ErrorSeverity]:
        """Classify an error into category and severity."""
        cls._initialize_rules()

        # Create text to analyze
        analysis_text = f"{exception_type} {message}"
        if stack_trace:
            analysis_text += f" {stack_trace[:500]}"

        return next(
            (
                (category, severity)
                for pattern, (
                    category,
                    severity,
                ) in cls.CLASSIFICATION_RULES.items()
                if pattern.search(analysis_text)
            ),
            (ErrorCategory.UNKNOWN_ERROR, ErrorSeverity.MEDIUM),
        )


class ErrorRateTracker:
    """Tracks error rates over time."""

    def __init__(self, window_minutes: int = 60, bucket_size_seconds: int = 60) -> None:
        """Initialize rate tracker.

        Args:
            window_minutes: Size of tracking window in minutes
            bucket_size_seconds: Size of each bucket in seconds

        """
        self.window_minutes = window_minutes
        self.bucket_size_seconds = bucket_size_seconds
        self.buckets_count = (window_minutes * 60) // bucket_size_seconds

        # Circular buffer for error counts per bucket
        self.error_buckets: deque[int] = deque([0] * self.buckets_count, maxlen=self.buckets_count)
        self.current_bucket_start = time.time()
        self.total_errors = 0

    def record_error(self) -> None:
        """Record an error occurrence."""
        now = time.time()

        # Check if we need to advance to a new bucket
        elapsed = now - self.current_bucket_start
        if elapsed >= self.bucket_size_seconds:
            # Calculate how many buckets to advance
            buckets_to_advance = min(int(elapsed // self.bucket_size_seconds), self.buckets_count)

            # Add empty buckets for the time that passed
            for _ in range(buckets_to_advance):
                self.error_buckets.append(0)

            # Update bucket start time
            self.current_bucket_start += buckets_to_advance * self.bucket_size_seconds

        # Increment current bucket
        if self.error_buckets:
            self.error_buckets[-1] += 1

        self.total_errors += 1

    def get_error_rate(self) -> float:
        """Get current error rate per minute."""
        total_errors_in_window = sum(self.error_buckets)
        return (total_errors_in_window / self.window_minutes) if self.window_minutes > 0 else 0.0

    def get_trend(self) -> str:
        """Get current error rate trend."""
        if len(self.error_buckets) < MIN_BUCKETS_FOR_TREND_ANALYSIS:
            return "stable"

        # Compare recent buckets to older ones
        recent_rate = sum(list(self.error_buckets)[-2:]) / 2
        older_rate = sum(list(self.error_buckets)[-4:-2]) / 2

        if recent_rate > older_rate * 2:
            return "spike"
        if recent_rate > older_rate * 1.5:
            return "increasing"
        return "decreasing" if recent_rate < older_rate * 0.5 else "stable"


class ErrorPatternDetector:
    """Detects patterns in error occurrences."""

    def __init__(self, min_occurrences: int = 3, time_window_minutes: int = 60) -> None:
        """Initialize pattern detector.

        Args:
            min_occurrences: Minimum occurrences to consider a pattern
            time_window_minutes: Time window for pattern detection

        """
        self.min_occurrences = min_occurrences
        self.time_window = timedelta(minutes=time_window_minutes)
        self.pattern_counts: dict[str, list[datetime]] = defaultdict(list)
        self.detected_patterns: dict[str, ErrorPattern] = {}

    def record_error(self, error: ErrorEvent) -> None:
        """Record an error for pattern detection."""
        signature = error.signature
        now = error.timestamp

        # Add to pattern counts
        self.pattern_counts[signature].append(now)

        # Clean old occurrences
        cutoff_time = now - self.time_window
        self.pattern_counts[signature] = [ts for ts in self.pattern_counts[signature] if ts >= cutoff_time]

        # Check if this forms a pattern
        count = len(self.pattern_counts[signature])
        if count >= self.min_occurrences:
            self._update_pattern(signature, error, count)

    def _update_pattern(self, signature: str, error: ErrorEvent, count: int) -> None:
        """Update detected pattern."""
        timestamps = self.pattern_counts[signature]

        # Calculate trend
        if len(timestamps) >= MIN_TIMESTAMPS_FOR_PATTERN_DETECTION:
            recent_count = len([ts for ts in timestamps if ts >= error.timestamp - timedelta(minutes=10)])
            older_count = len([ts for ts in timestamps if error.timestamp - timedelta(minutes=20) <= ts < error.timestamp - timedelta(minutes=10)])

            if recent_count > older_count * 2:
                trend = "spike"
            elif recent_count > older_count * 1.5:
                trend = "increasing"
            elif recent_count < older_count * 0.5:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Calculate impact score (higher for more frequent, recent, and severe errors)
        time_factor = 1.0
        if timestamps:
            minutes_since_last = (error.timestamp - max(timestamps[:-1], default=timestamps[-1])).total_seconds() / 60
            time_factor = max(0.1, 1.0 - (minutes_since_last / 60))  # Decays over 1 hour

        severity_factor = {
            ErrorSeverity.LOW: 0.25,
            ErrorSeverity.MEDIUM: 0.5,
            ErrorSeverity.HIGH: 0.75,
            ErrorSeverity.CRITICAL: 1.0,
        }[error.severity]

        impact_score = count * time_factor * severity_factor

        # Update or create pattern
        if signature in self.detected_patterns:
            pattern = self.detected_patterns[signature]
            pattern.count = count
            pattern.last_seen = error.timestamp
            pattern.trend = trend
            pattern.impact_score = impact_score
        else:
            self.detected_patterns[signature] = ErrorPattern(
                signature=signature,
                category=error.category,
                severity=error.severity,
                count=count,
                first_seen=min(timestamps),
                last_seen=error.timestamp,
                sample_message=error.message[:200],
                trend=trend,
                impact_score=impact_score,
            )

    def get_active_patterns(self, min_impact: float = 0.0) -> list[ErrorPattern]:
        """Get currently active error patterns."""
        now = datetime.now(UTC)
        cutoff_time = now - self.time_window

        active_patterns = [
            pattern for pattern in self.detected_patterns.values() if pattern.last_seen >= cutoff_time and pattern.impact_score >= min_impact
        ]

        # Sort by impact score descending
        return sorted(active_patterns, key=lambda p: p.impact_score, reverse=True)


class ErrorMetricsCollector:
    """Main error metrics collection and analysis system."""

    def __init__(self, window_minutes: int = 60) -> None:
        """Initialize error metrics collector."""
        self.window_minutes = window_minutes
        self.classifier = ErrorClassifier()
        self.rate_tracker = ErrorRateTracker(window_minutes)
        self.pattern_detector = ErrorPatternDetector(time_window_minutes=window_minutes)

        # Store recent errors
        self.recent_errors: deque[ErrorEvent] = deque(maxlen=1000)

        # Alert handlers
        self.alert_handlers: list[Callable[[str, ErrorSeverity, dict[str, Any]], None]] = []

    def record_error(
        self,
        request: ErrorRecordingRequest | None = None,
        exception: Exception | None = None,
        message: str | None = None,
    ) -> ErrorEvent:
        """Record an error event."""
        now = datetime.now(UTC)

        # Use request object or fall back to individual parameters
        if request is None:
            request = ErrorRecordingRequest(exception=exception, message=message)

        # Extract information from exception
        if request.exception:
            exception_type = type(request.exception).__name__
            error_message = str(request.exception)
            stack_trace = repr(request.exception)  # Simple representation
        else:
            exception_type = "UnknownError"
            error_message = request.message or "Unknown error"
            stack_trace = None

        # Classify error if not provided
        if request.category is None or request.severity is None:
            classified_category, classified_severity = self.classifier.classify_error(exception_type, error_message, stack_trace)
            category = request.category or classified_category
            severity = request.severity or classified_severity
        else:
            category = request.category
            severity = request.severity

        # Create error event
        error_event = ErrorEvent(
            timestamp=now,
            category=category,
            severity=severity,
            message=error_message,
            exception_type=exception_type,
            stack_trace=stack_trace,
            context=request.context or {},
            source_module=request.source_module,
            error_code=request.error_code,
            user_id=request.user_id,
        )

        # Record in various trackers
        self.recent_errors.append(error_event)
        self.rate_tracker.record_error()
        self.pattern_detector.record_error(error_event)

        # Check for alerts
        self._check_alerts(error_event)

        # Log error
        log_level = {
            ErrorSeverity.LOW: logging.INFO,
            ErrorSeverity.MEDIUM: logging.WARNING,
            ErrorSeverity.HIGH: logging.ERROR,
            ErrorSeverity.CRITICAL: logging.CRITICAL,
        }[severity]

        logger.log(
            log_level,
            "ERROR [%s/%s]: %s",
            category.value.upper(),
            severity.value.upper(),
            error_message,
        )

        return error_event

    def _check_alerts(self, error: ErrorEvent) -> None:
        """Check if error should trigger alerts."""
        current_rate = self.rate_tracker.get_error_rate()
        trend = self.rate_tracker.get_trend()

        alert_data: dict[str, Any] = {
            "error_rate": current_rate,
            "trend": trend,
            "category": error.category.value,
            "severity": error.severity.value,
            "message": error.message,
            "timestamp": error.timestamp.isoformat(),
        }

        # High error rate alert
        if current_rate > HIGH_ERROR_RATE_THRESHOLD_PER_MINUTE:
            self._trigger_alert("High Error Rate", ErrorSeverity.HIGH, alert_data)

        # Spike detection
        if trend == "spike":
            self._trigger_alert("Error Rate Spike", ErrorSeverity.CRITICAL, alert_data)

        # Critical error alert
        if error.severity == ErrorSeverity.CRITICAL:
            self._trigger_alert("Critical Error", ErrorSeverity.CRITICAL, alert_data)

    def _trigger_alert(self, alert_type: str, severity: ErrorSeverity, data: dict[str, Any]) -> None:
        """Trigger an alert."""
        for handler in self.alert_handlers:
            try:
                handler(alert_type, severity, data)
            except (TypeError, ValueError, AttributeError, OSError):
                logger.exception("Alert handler failed")
            except (RuntimeError, ImportError, KeyError, IndexError):
                # Catch any other unexpected exceptions to prevent monitoring disruption
                logger.exception("Unexpected error in alert handler")

    def add_alert_handler(self, handler: Callable[[str, ErrorSeverity, dict[str, Any]], None]) -> None:
        """Add an alert handler."""
        self.alert_handlers.append(handler)

    def get_error_stats(self, time_window: timedelta | None = None) -> ErrorStats:
        """Get error statistics for a time period."""
        if time_window is None:
            time_window = timedelta(minutes=self.window_minutes)

        cutoff_time = datetime.now(UTC) - time_window
        recent_errors = [e for e in self.recent_errors if e.timestamp >= cutoff_time]

        stats = ErrorStats(time_window=time_window)
        stats.total_errors = len(recent_errors)

        # Count by category and severity
        for error in recent_errors:
            stats.errors_by_category[error.category] += 1
            stats.errors_by_severity[error.severity] += 1

        # Calculate error rate
        window_minutes = time_window.total_seconds() / 60
        stats.error_rate_per_minute = stats.total_errors / window_minutes if window_minutes > 0 else 0

        # Count unique errors
        unique_signatures = {error.signature for error in recent_errors}
        stats.unique_errors = len(unique_signatures)

        # Get top patterns
        stats.top_patterns = self.pattern_detector.get_active_patterns()[:10]

        return stats

    def get_summary(self) -> dict[str, Any]:
        """Get comprehensive error metrics summary."""
        stats = self.get_error_stats()

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "window_minutes": self.window_minutes,
            "total_errors": stats.total_errors,
            "error_rate_per_minute": round(stats.error_rate_per_minute, 2),
            "unique_errors": stats.unique_errors,
            "trend": self.rate_tracker.get_trend(),
            "errors_by_category": {cat.value: count for cat, count in stats.errors_by_category.items()},
            "errors_by_severity": {sev.value: count for sev, count in stats.errors_by_severity.items()},
            "top_patterns": [
                {
                    "signature": pattern.signature,
                    "category": pattern.category.value,
                    "severity": pattern.severity.value,
                    "count": pattern.count,
                    "trend": pattern.trend,
                    "impact_score": round(pattern.impact_score, 2),
                    "sample_message": pattern.sample_message,
                }
                for pattern in stats.top_patterns[:5]
            ],
        }


def _create_error_collector_getter() -> Callable[[], ErrorMetricsCollector]:
    """Create an error collector getter function with closure-based singleton."""
    instance: ErrorMetricsCollector | None = None

    def _get_collector() -> ErrorMetricsCollector:
        """Get or create the singleton ErrorMetricsCollector instance."""
        nonlocal instance
        if instance is None:
            instance = ErrorMetricsCollector()
        return instance

    return _get_collector


# Create the actual function that external code will use
get_error_collector = _create_error_collector_getter()


def default_error_alert_handler(alert_type: str, severity: ErrorSeverity, data: dict[str, Any]) -> None:
    """Log error alerts with appropriate severity levels."""
    level_map = {
        ErrorSeverity.LOW: logging.INFO,
        ErrorSeverity.MEDIUM: logging.WARNING,
        ErrorSeverity.HIGH: logging.ERROR,
        ErrorSeverity.CRITICAL: logging.CRITICAL,
    }

    logger.log(
        level_map[severity],
        "ERROR ALERT [%s]: %s (Rate: %.1f/min)",
        severity.value.upper(),
        alert_type,
        data.get("error_rate", 0),
    )


# Setup default alert handler
get_error_collector().add_alert_handler(default_error_alert_handler)
