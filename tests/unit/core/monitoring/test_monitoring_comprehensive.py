"""Comprehensive unit tests for monitoring module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from src.metrics.monitoring import (
    Alert,
    AlertLevel,
    AlertManager,
    AlertRule,
    MetricsCollector,
    OperationContext,
    PerformanceMetric,
    PerformanceTracker,
    ResourceMetric,
    ResourceMonitor,
    ResourceType,
    ThresholdRule,
    get_metrics_collector,
    log_alert_handler,
)


def _make_performance_metric(name: str, value: float) -> PerformanceMetric:
    """Create a performance metric with current timestamp."""
    return PerformanceMetric(
        name=name,
        value=value,
        timestamp=datetime.now(UTC),
    )


def _make_resource_metric(resource_type: ResourceType, usage: float) -> ResourceMetric:
    """Create a resource metric with default values."""
    return ResourceMetric(
        resource_type=resource_type,
        usage_percent=usage,
        available=100,
        total=200,
        timestamp=datetime.now(UTC),
    )


def test_alert_level_values() -> None:
    """Ensure alert level enum exposes expected values."""
    levels = {level.value for level in AlertLevel}
    assert levels == {"info", "warning", "error", "critical"}


def test_resource_type_values() -> None:
    """Ensure resource type enum exposes expected values."""
    resources = {resource.value for resource in ResourceType}
    assert resources == {"cpu", "memory", "disk", "network"}


@pytest.fixture
def performance_tracker() -> PerformanceTracker:
    """Instantiate a fresh performance tracker."""
    return PerformanceTracker()


def test_performance_tracker_operation_flow(performance_tracker: PerformanceTracker) -> None:
    """Verify start/end operation records metrics and clears timers."""
    performance_tracker.start_operation("sync_library")
    assert "sync_library" in performance_tracker.operation_timers

    duration = performance_tracker.end_operation("sync_library", tags={"stage": "load"})
    assert duration >= 0.0
    assert "sync_library" not in performance_tracker.operation_timers

    recorded = performance_tracker.metrics[-1]
    assert recorded.name == "operation_duration_sync_library"
    assert recorded.tags == {"stage": "load"}


def test_performance_tracker_counters_and_gauges(performance_tracker: PerformanceTracker) -> None:
    """Ensure counters and gauges aggregate correctly."""
    performance_tracker.record_counter("processed_tracks")
    performance_tracker.record_counter("processed_tracks", increment=2)
    performance_tracker.record_gauge("queue_depth", value=5.5, unit="items")

    names = [metric.name for metric in performance_tracker.metrics]
    assert names.count("processed_tracks") == 2
    assert performance_tracker.counters["processed_tracks"] == 3
    assert "queue_depth" in names


def test_performance_tracker_get_and_clear_metrics(performance_tracker: PerformanceTracker) -> None:
    """Check metric retrieval with filters and clearing state."""
    performance_tracker.record_gauge("latency_ms", 12.0)
    performance_tracker.metrics[-1].timestamp = datetime.now(UTC) - timedelta(minutes=2)
    performance_tracker.record_gauge("latency_ms", 8.0)

    recent = performance_tracker.get_metrics(since=datetime.now(UTC) - timedelta(minutes=1))
    assert len(recent) == 1
    assert recent[0].value == 8.0

    performance_tracker.clear_metrics()
    assert performance_tracker.metrics == []


@pytest.fixture
def resource_monitor(monkeypatch: pytest.MonkeyPatch) -> ResourceMonitor:
    """Create resource monitor with psutil disabled for deterministic data."""
    monkeypatch.setattr("src.metrics.monitoring.psutil_available", False)
    return ResourceMonitor()


def test_resource_monitor_collectors(resource_monitor: ResourceMonitor) -> None:
    """Ensure resource collectors return fallback data when psutil unavailable."""
    cpu = resource_monitor.collect_cpu_metrics()
    memory = resource_monitor.collect_memory_metrics()
    disk = resource_monitor.collect_disk_metrics()

    assert cpu.resource_type == ResourceType.CPU
    assert memory.resource_type == ResourceType.MEMORY
    assert disk.resource_type == ResourceType.DISK
    assert cpu.metadata["psutil_unavailable"] is True


def test_resource_monitor_filters(resource_monitor: ResourceMonitor) -> None:
    """Validate retrieval filters by type and timestamp."""
    metrics = resource_monitor.collect_all_metrics()
    resource_monitor.metrics.extend(metrics)
    metrics[0].timestamp = datetime.now(UTC) - timedelta(minutes=10)

    cpu_only = resource_monitor.get_metrics(resource_type=ResourceType.CPU)
    assert len(cpu_only) == 1

    recent = resource_monitor.get_metrics(since=datetime.now(UTC) - timedelta(minutes=5))
    assert len(recent) == 2  # memory and disk remain within the recent window

    resource_monitor.clear_metrics()
    assert resource_monitor.metrics == []


@pytest.fixture
def alert_manager_instance() -> AlertManager:
    """Instantiate a fresh alert manager."""
    return AlertManager()


def test_alert_manager_rules_and_handlers(alert_manager_instance: AlertManager) -> None:
    """Ensure rules evaluate metrics and trigger handlers."""
    captured: list[Alert] = []
    alert_manager_instance.add_rule(ThresholdRule("High CPU", threshold=70.0, level=AlertLevel.WARNING))
    alert_manager_instance.add_handler(captured.append)

    metric = _make_performance_metric("cpu_usage", 85.0)
    alerts = alert_manager_instance.evaluate_metrics([metric])

    assert len(alerts) == 1
    assert alerts[0].level == AlertLevel.WARNING
    assert captured[-1] is alerts[0]


def test_alert_manager_filters_and_clear(alert_manager_instance: AlertManager) -> None:
    """Check alert retrieval by level and clearing stored alerts."""
    warning_alert = Alert(
        level=AlertLevel.WARNING,
        message="Test warning",
        timestamp=datetime.now(UTC),
        source="unit-test",
        metric_name="metric",
        value=1.0,
        threshold=0.5,
    )
    error_alert = Alert(
        level=AlertLevel.ERROR,
        message="Test error",
        timestamp=datetime.now(UTC),
        source="unit-test",
        metric_name="metric",
        value=2.0,
        threshold=1.0,
    )
    alert_manager_instance.alerts.extend([warning_alert, error_alert])

    filtered = alert_manager_instance.get_alerts(level=AlertLevel.ERROR)
    assert filtered == [error_alert]

    alert_manager_instance.clear_alerts()
    assert alert_manager_instance.alerts == []


@pytest.fixture
def metrics_collector(monkeypatch: pytest.MonkeyPatch) -> MetricsCollector:
    """Provide metrics collector with deterministic resource data."""
    monkeypatch.setattr("src.metrics.monitoring.psutil_available", False)
    return MetricsCollector()


@pytest.fixture
def collector_performance_tracker(metrics_collector: MetricsCollector) -> PerformanceTracker:
    """Get performance tracker from metrics collector for integration testing."""
    # Access via __dict__ to avoid IDE fixture detection warning
    tracker = metrics_collector.__dict__["performance_tracker"]
    assert isinstance(tracker, PerformanceTracker)
    return tracker


def test_metrics_collector_operation_context(collector_performance_tracker: PerformanceTracker) -> None:
    """Verify operation context records success and failure counters."""
    with OperationContext(collector_performance_tracker, "sync_tracks", tags={"source": "test"}):
        pass

    names = [metric.name for metric in collector_performance_tracker.metrics]
    assert "operation_duration_sync_tracks" in names
    assert "operation_success_sync_tracks" in names

    error_message = "failure"
    with pytest.raises(RuntimeError), OperationContext(collector_performance_tracker, "sync_tracks"):
        raise RuntimeError(error_message)

    error_names = [metric.name for metric in collector_performance_tracker.metrics]
    assert "operation_errors_sync_tracks" in error_names


def test_metrics_collector_summary(metrics_collector: MetricsCollector) -> None:
    """Ensure summary aggregates recent metrics and alerts."""
    metrics_collector.record_counter("processed", increment=5)
    metrics_collector.record_gauge("latency_ms", value=10.5)

    cpu_metric = _make_resource_metric(ResourceType.CPU, usage=92.0)
    metrics_collector.alert_manager.evaluate_metrics([cpu_metric])

    summary = metrics_collector.get_summary()
    assert summary["monitoring_status"] == "stopped"
    assert summary["alerts"]["total"] >= 0
    assert "operations" in summary["performance"]
    assert "latest" in summary["resources"]


def test_metrics_collector_custom_rule(metrics_collector: MetricsCollector) -> None:
    """Validate custom rules and handlers plug into collector."""
    captured: list[Alert] = []

    class AlwaysTriggerRule(AlertRule):
        def __init__(self) -> None:
            super().__init__("Always", threshold=0.0, level=AlertLevel.ERROR)

        def evaluate(self, metric: PerformanceMetric | ResourceMetric) -> Alert | None:
            return Alert(
                level=self.level,
                message="Triggered",
                timestamp=datetime.now(UTC),
                source="AlwaysRule",
                metric_name="test_metric",
                value=metric.value if isinstance(metric, PerformanceMetric) else metric.usage_percent,
                threshold=self.threshold,
            )

    metrics_collector.add_alert_rule(AlwaysTriggerRule())
    metrics_collector.add_alert_handler(captured.append)
    metrics_collector.alert_manager.evaluate_metrics([_make_performance_metric("test", 1.0)])

    assert captured
    assert captured[-1].message == "Triggered"


def test_get_metrics_collector_singleton() -> None:
    """Confirm get_metrics_collector returns singleton instance."""
    first = get_metrics_collector()
    second = get_metrics_collector()
    assert first is second


def test_log_alert_handler_logs() -> None:
    """Ensure log_alert_handler delegates to logging with expected severity."""
    alert = Alert(
        level=AlertLevel.CRITICAL,
        message="Critical issue",
        timestamp=datetime.now(UTC),
        source="unit-test",
        metric_name="metric",
        value=100.0,
        threshold=50.0,
    )

    with patch("src.metrics.monitoring.logger") as mock_logger:
        log_alert_handler(alert)
        mock_logger.log.assert_called()
