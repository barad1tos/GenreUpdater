"""Tests for src/metrics/monitoring.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from metrics.monitoring import (
    Alert,
    AlertLevel,
    AlertManager,
    MetricsCollector,
    OperationContext,
    PerformanceMetric,
    PerformanceTracker,
    ResourceMetric,
    ResourceMonitor,
    ResourceType,
    ThresholdRule,
)


class TestAlertLevel:
    """Tests for AlertLevel enum."""

    def test_alert_level_values(self) -> None:
        """Test AlertLevel has expected values."""
        assert AlertLevel.INFO.value == "info"
        assert AlertLevel.WARNING.value == "warning"
        assert AlertLevel.ERROR.value == "error"
        assert AlertLevel.CRITICAL.value == "critical"

    def test_alert_level_count(self) -> None:
        """Test AlertLevel has 4 levels."""
        assert len(AlertLevel) == 4


class TestResourceType:
    """Tests for ResourceType enum."""

    def test_resource_type_values(self) -> None:
        """Test ResourceType has expected values."""
        assert ResourceType.CPU.value == "cpu"
        assert ResourceType.MEMORY.value == "memory"
        assert ResourceType.DISK.value == "disk"
        assert ResourceType.NETWORK.value == "network"

    def test_resource_type_count(self) -> None:
        """Test ResourceType has 4 types."""
        assert len(ResourceType) == 4


class TestPerformanceMetric:
    """Tests for PerformanceMetric dataclass."""

    def test_performance_metric_creation(self) -> None:
        """Test PerformanceMetric creation with all fields."""
        now = datetime.now(UTC)
        metric = PerformanceMetric(
            name="test_metric",
            value=42.5,
            timestamp=now,
            unit="seconds",
            tags={"env": "test"},
        )
        assert metric.name == "test_metric"
        assert metric.value == 42.5
        assert metric.timestamp == now
        assert metric.unit == "seconds"
        assert metric.tags == {"env": "test"}

    def test_performance_metric_defaults(self) -> None:
        """Test PerformanceMetric default values."""
        metric = PerformanceMetric(
            name="test",
            value=1.0,
            timestamp=datetime.now(UTC),
        )
        assert metric.unit == ""
        assert metric.tags == {}


class TestResourceMetric:
    """Tests for ResourceMetric dataclass."""

    def test_resource_metric_creation(self) -> None:
        """Test ResourceMetric creation with all fields."""
        now = datetime.now(UTC)
        metric = ResourceMetric(
            resource_type=ResourceType.CPU,
            usage_percent=75.5,
            available=25,
            total=100,
            timestamp=now,
            metadata={"cpu_count": 8},
        )
        assert metric.resource_type == ResourceType.CPU
        assert metric.usage_percent == 75.5
        assert metric.available == 25
        assert metric.total == 100
        assert metric.metadata == {"cpu_count": 8}


class TestAlert:
    """Tests for Alert dataclass."""

    def test_alert_creation(self) -> None:
        """Test Alert creation with all fields."""
        now = datetime.now(UTC)
        alert = Alert(
            level=AlertLevel.WARNING,
            message="High CPU usage",
            timestamp=now,
            source="ThresholdRule",
            metric_name="cpu",
            value=85.0,
            threshold=80.0,
        )
        assert alert.level == AlertLevel.WARNING
        assert alert.message == "High CPU usage"
        assert alert.source == "ThresholdRule"
        assert alert.value == 85.0
        assert alert.threshold == 80.0


class TestPerformanceTracker:
    """Tests for PerformanceTracker class."""

    @pytest.fixture
    def tracker(self) -> PerformanceTracker:
        """Create a PerformanceTracker instance."""
        return PerformanceTracker()

    def test_init_empty_collections(self, tracker: PerformanceTracker) -> None:
        """Test tracker initializes with empty collections."""
        assert tracker.metrics == []
        assert tracker.operation_timers == {}
        assert tracker.counters == {}

    def test_start_operation(self, tracker: PerformanceTracker) -> None:
        """Test starting an operation timer."""
        tracker.start_operation("test_op")
        assert "test_op" in tracker.operation_timers
        assert isinstance(tracker.operation_timers["test_op"], float)

    def test_end_operation(self, tracker: PerformanceTracker) -> None:
        """Test ending an operation timer records metric."""
        tracker.start_operation("test_op")
        duration = tracker.end_operation("test_op")

        assert duration >= 0
        assert "test_op" not in tracker.operation_timers
        assert len(tracker.metrics) == 1
        assert "operation_duration_test_op" in tracker.metrics[0].name

    def test_end_operation_not_started(self, tracker: PerformanceTracker) -> None:
        """Test ending operation that wasn't started returns 0."""
        duration = tracker.end_operation("nonexistent")
        assert duration == 0.0
        assert not tracker.metrics

    def test_end_operation_with_tags(self, tracker: PerformanceTracker) -> None:
        """Test ending operation with tags."""
        tracker.start_operation("tagged_op")
        tracker.end_operation("tagged_op", tags={"env": "test"})

        assert tracker.metrics[0].tags == {"env": "test"}

    def test_record_counter(self, tracker: PerformanceTracker) -> None:
        """Test recording counter increments."""
        tracker.record_counter("requests")
        assert tracker.counters["requests"] == 1

        tracker.record_counter("requests", increment=5)
        assert tracker.counters["requests"] == 6

    def test_record_counter_creates_metric(self, tracker: PerformanceTracker) -> None:
        """Test record_counter creates a metric."""
        tracker.record_counter("test_counter")
        assert len(tracker.metrics) == 1
        assert tracker.metrics[0].name == "test_counter"
        assert tracker.metrics[0].unit == "count"

    def test_record_gauge(self, tracker: PerformanceTracker) -> None:
        """Test recording gauge values."""
        tracker.record_gauge("temperature", 72.5, unit="fahrenheit")

        assert len(tracker.metrics) == 1
        assert tracker.metrics[0].name == "temperature"
        assert tracker.metrics[0].value == 72.5
        assert tracker.metrics[0].unit == "fahrenheit"

    def test_get_metrics_all(self, tracker: PerformanceTracker) -> None:
        """Test getting all metrics."""
        tracker.record_gauge("metric1", 1.0)
        tracker.record_gauge("metric2", 2.0)

        metrics = tracker.get_metrics()
        assert len(metrics) == 2

    def test_get_metrics_since(self, tracker: PerformanceTracker) -> None:
        """Test getting metrics since a specific time."""
        tracker.record_gauge("old_metric", 1.0)

        # Get metrics from now (should exclude old one if we set since to future)
        future = datetime.now(UTC) + timedelta(hours=1)
        metrics = tracker.get_metrics(since=future)
        assert not metrics

    def test_clear_metrics(self, tracker: PerformanceTracker) -> None:
        """Test clearing all metrics."""
        tracker.record_gauge("metric1", 1.0)
        tracker.record_gauge("metric2", 2.0)

        tracker.clear_metrics()
        assert not tracker.metrics


class TestResourceMonitor:
    """Tests for ResourceMonitor class."""

    @pytest.fixture
    def monitor(self) -> ResourceMonitor:
        """Create a ResourceMonitor instance."""
        return ResourceMonitor()

    def test_init_empty_metrics(self, monitor: ResourceMonitor) -> None:
        """Test monitor initializes with empty metrics."""
        assert monitor.metrics == []

    def test_collect_cpu_metrics_without_psutil(self, monitor: ResourceMonitor) -> None:
        """Test CPU metrics collection when psutil unavailable."""
        with patch("metrics.monitoring.psutil_available", False):
            metric = monitor.collect_cpu_metrics()

            assert metric.resource_type == ResourceType.CPU
            assert metric.usage_percent == 0.0
            assert metric.metadata.get("psutil_unavailable") is True

    def test_collect_memory_metrics_without_psutil(self, monitor: ResourceMonitor) -> None:
        """Test memory metrics collection when psutil unavailable."""
        with patch("metrics.monitoring.psutil_available", False):
            metric = monitor.collect_memory_metrics()

            assert metric.resource_type == ResourceType.MEMORY
            assert metric.usage_percent == 0.0
            assert metric.metadata.get("psutil_unavailable") is True

    def test_collect_disk_metrics_without_psutil(self, monitor: ResourceMonitor) -> None:
        """Test disk metrics collection when psutil unavailable."""
        with patch("metrics.monitoring.psutil_available", False):
            metric = monitor.collect_disk_metrics()

            assert metric.resource_type == ResourceType.DISK
            assert metric.usage_percent == 0.0
            assert metric.metadata.get("psutil_unavailable") is True

    def test_collect_cpu_metrics_with_psutil(self, monitor: ResourceMonitor) -> None:
        """Test CPU metrics collection with mocked psutil."""
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 50.0
        mock_psutil.cpu_count.return_value = 4
        mock_psutil.cpu_freq.return_value = None

        with (
            patch("metrics.monitoring.psutil_available", True),
            patch("metrics.monitoring._get_psutil", return_value=mock_psutil),
        ):
            metric = monitor.collect_cpu_metrics()

            assert metric.resource_type == ResourceType.CPU
            assert metric.usage_percent == 50.0
            assert metric.metadata["cpu_count"] == 4

    def test_collect_memory_metrics_with_psutil(self, monitor: ResourceMonitor) -> None:
        """Test memory metrics collection with mocked psutil."""
        mock_memory = MagicMock()
        mock_memory.percent = 60.0
        mock_memory.available = 4_000_000_000
        mock_memory.total = 10_000_000_000
        mock_memory.used = 6_000_000_000

        mock_psutil = MagicMock()
        mock_psutil.virtual_memory.return_value = mock_memory

        with (
            patch("metrics.monitoring.psutil_available", True),
            patch("metrics.monitoring._get_psutil", return_value=mock_psutil),
        ):
            metric = monitor.collect_memory_metrics()

            assert metric.resource_type == ResourceType.MEMORY
            assert metric.usage_percent == 60.0
            assert metric.total == 10_000_000_000

    def test_collect_disk_metrics_with_psutil(self, monitor: ResourceMonitor) -> None:
        """Test disk metrics collection with mocked psutil."""
        mock_disk = MagicMock()
        mock_disk.total = 500_000_000_000
        mock_disk.used = 250_000_000_000
        mock_disk.free = 250_000_000_000

        mock_psutil = MagicMock()
        mock_psutil.disk_usage.return_value = mock_disk

        with (
            patch("metrics.monitoring.psutil_available", True),
            patch("metrics.monitoring._get_psutil", return_value=mock_psutil),
        ):
            metric = monitor.collect_disk_metrics()

            assert metric.resource_type == ResourceType.DISK
            assert metric.usage_percent == 50.0
            assert metric.metadata["path"] == "/"

    def test_collect_all_metrics(self, monitor: ResourceMonitor) -> None:
        """Test collecting all resource metrics."""
        with patch("metrics.monitoring.psutil_available", False):
            metrics = monitor.collect_all_metrics()

            assert len(metrics) == 3
            resource_types = {m.resource_type for m in metrics}
            assert ResourceType.CPU in resource_types
            assert ResourceType.MEMORY in resource_types
            assert ResourceType.DISK in resource_types

    def test_get_metrics_filtered_by_type(self, monitor: ResourceMonitor) -> None:
        """Test filtering metrics by resource type."""
        # Directly add test metrics to avoid patch complexity
        now = datetime.now(UTC)
        monitor.metrics.append(
            ResourceMetric(
                resource_type=ResourceType.CPU,
                usage_percent=50.0,
                available=50,
                total=100,
                timestamp=now,
            )
        )
        monitor.metrics.append(
            ResourceMetric(
                resource_type=ResourceType.MEMORY,
                usage_percent=60.0,
                available=40,
                total=100,
                timestamp=now,
            )
        )

        cpu_metrics = monitor.get_metrics(resource_type=ResourceType.CPU)
        assert len(cpu_metrics) == 1
        assert cpu_metrics[0].resource_type == ResourceType.CPU

    def test_get_metrics_filtered_by_time(self, monitor: ResourceMonitor) -> None:
        """Test filtering metrics by time."""
        now = datetime.now(UTC)
        monitor.metrics.append(
            ResourceMetric(
                resource_type=ResourceType.CPU,
                usage_percent=50.0,
                available=50,
                total=100,
                timestamp=now,
            )
        )

        future = now + timedelta(hours=1)
        metrics = monitor.get_metrics(since=future)
        assert not metrics

        # Metrics from before should be included
        past = now - timedelta(hours=1)
        metrics = monitor.get_metrics(since=past)
        assert len(metrics) == 1

    def test_clear_metrics(self, monitor: ResourceMonitor) -> None:
        """Test clearing stored metrics."""
        now = datetime.now(UTC)
        monitor.metrics.append(
            ResourceMetric(
                resource_type=ResourceType.CPU,
                usage_percent=50.0,
                available=50,
                total=100,
                timestamp=now,
            )
        )
        monitor.metrics.append(
            ResourceMetric(
                resource_type=ResourceType.MEMORY,
                usage_percent=60.0,
                available=40,
                total=100,
                timestamp=now,
            )
        )
        assert len(monitor.metrics) == 2

        monitor.clear_metrics()
        assert not monitor.metrics


class TestThresholdRule:
    """Tests for ThresholdRule class."""

    def test_threshold_rule_greater(self) -> None:
        """Test threshold rule with 'greater' operator (default)."""
        rule = ThresholdRule("High CPU", 80.0, AlertLevel.WARNING)
        metric = PerformanceMetric(name="cpu", value=85.0, timestamp=datetime.now(UTC))

        alert = rule.evaluate(metric)
        assert alert is not None
        assert alert.level == AlertLevel.WARNING
        assert alert.value == 85.0

    def test_threshold_rule_greater_no_alert(self) -> None:
        """Test threshold rule doesn't alert when below threshold."""
        rule = ThresholdRule("High CPU", 80.0, AlertLevel.WARNING)
        metric = PerformanceMetric(name="cpu", value=75.0, timestamp=datetime.now(UTC))

        alert = rule.evaluate(metric)
        assert alert is None

    def test_threshold_rule_less(self) -> None:
        """Test threshold rule with 'less' operator."""
        rule = ThresholdRule("Low Memory", 10.0, AlertLevel.WARNING, operator="less")
        metric = PerformanceMetric(name="memory", value=5.0, timestamp=datetime.now(UTC))

        alert = rule.evaluate(metric)
        assert alert is not None

    def test_threshold_rule_equal(self) -> None:
        """Test threshold rule with 'equal' operator."""
        rule = ThresholdRule("Exact Value", 50.0, AlertLevel.INFO, operator="equal")
        metric = PerformanceMetric(name="gauge", value=50.0, timestamp=datetime.now(UTC))

        alert = rule.evaluate(metric)
        assert alert is not None

    def test_threshold_rule_resource_metric(self) -> None:
        """Test threshold rule with ResourceMetric uses usage_percent."""
        rule = ThresholdRule("High Disk", 90.0, AlertLevel.WARNING)
        metric = ResourceMetric(
            resource_type=ResourceType.DISK,
            usage_percent=95.0,
            available=50,
            total=1000,
            timestamp=datetime.now(UTC),
        )

        alert = rule.evaluate(metric)
        assert alert is not None
        assert alert.value == 95.0

    def test_threshold_rule_invalid_operator(self) -> None:
        """Test threshold rule raises on invalid operator."""
        rule = ThresholdRule("Test", 50.0, AlertLevel.WARNING, operator="invalid")
        metric = PerformanceMetric(name="test", value=60.0, timestamp=datetime.now(UTC))

        with pytest.raises(ValueError, match="Unrecognized operator"):
            rule.evaluate(metric)


class TestAlertManager:
    """Tests for AlertManager class."""

    @pytest.fixture
    def manager(self) -> AlertManager:
        """Create an AlertManager instance."""
        return AlertManager()

    def test_init_empty_collections(self, manager: AlertManager) -> None:
        """Test manager initializes with empty collections."""
        assert manager.rules == []
        assert manager.alerts == []
        assert manager.handlers == []

    def test_add_rule(self, manager: AlertManager) -> None:
        """Test adding an alert rule."""
        rule = ThresholdRule("Test Rule", 80.0, AlertLevel.WARNING)
        manager.add_rule(rule)

        assert len(manager.rules) == 1
        assert manager.rules[0] is rule

    def test_add_handler(self, manager: AlertManager) -> None:
        """Test adding an alert handler."""
        handler = MagicMock()
        manager.add_handler(handler)

        assert len(manager.handlers) == 1

    def test_evaluate_metrics_triggers_alert(self, manager: AlertManager) -> None:
        """Test evaluating metrics triggers alerts."""
        rule = ThresholdRule("High Value", 50.0, AlertLevel.WARNING)
        manager.add_rule(rule)

        metric = PerformanceMetric(name="test", value=75.0, timestamp=datetime.now(UTC))
        alerts = manager.evaluate_metrics([metric])

        assert len(alerts) == 1
        assert len(manager.alerts) == 1

    def test_evaluate_metrics_calls_handlers(self, manager: AlertManager) -> None:
        """Test evaluating metrics calls handlers."""
        rule = ThresholdRule("Test", 50.0, AlertLevel.WARNING)
        manager.add_rule(rule)

        handler = MagicMock()
        manager.add_handler(handler)

        metric = PerformanceMetric(name="test", value=75.0, timestamp=datetime.now(UTC))
        manager.evaluate_metrics([metric])

        handler.assert_called_once()

    def test_evaluate_metrics_handler_exception_logged(self, manager: AlertManager) -> None:
        """Test handler exceptions are caught and logged."""
        rule = ThresholdRule("Test", 50.0, AlertLevel.WARNING)
        manager.add_rule(rule)

        def failing_handler(_alert: Alert) -> None:
            """Test handler that raises."""
            raise RuntimeError("Handler failed")

        manager.add_handler(failing_handler)

        metric = PerformanceMetric(name="test", value=75.0, timestamp=datetime.now(UTC))
        # Should not raise
        alerts = manager.evaluate_metrics([metric])
        assert len(alerts) == 1

    def test_get_alerts_filtered_by_level(self, manager: AlertManager) -> None:
        """Test filtering alerts by level."""
        rule_warn = ThresholdRule("Warn", 50.0, AlertLevel.WARNING)
        rule_crit = ThresholdRule("Crit", 80.0, AlertLevel.CRITICAL)
        manager.add_rule(rule_warn)
        manager.add_rule(rule_crit)

        metrics: list[PerformanceMetric | ResourceMetric] = [
            PerformanceMetric(name="test1", value=60.0, timestamp=datetime.now(UTC)),
            PerformanceMetric(name="test2", value=90.0, timestamp=datetime.now(UTC)),
        ]
        manager.evaluate_metrics(metrics)

        warning_alerts = manager.get_alerts(level=AlertLevel.WARNING)
        critical_alerts = manager.get_alerts(level=AlertLevel.CRITICAL)

        assert len(warning_alerts) == 2  # Both metrics trigger warning
        assert len(critical_alerts) == 1  # Only 90.0 triggers critical

    def test_get_alerts_filtered_by_time(self, manager: AlertManager) -> None:
        """Test filtering alerts by time."""
        rule = ThresholdRule("Test", 50.0, AlertLevel.WARNING)
        manager.add_rule(rule)

        metric = PerformanceMetric(name="test", value=75.0, timestamp=datetime.now(UTC))
        manager.evaluate_metrics([metric])

        future = datetime.now(UTC) + timedelta(hours=1)
        alerts = manager.get_alerts(since=future)
        assert not alerts

    def test_clear_alerts(self, manager: AlertManager) -> None:
        """Test clearing stored alerts."""
        rule = ThresholdRule("Test", 50.0, AlertLevel.WARNING)
        manager.add_rule(rule)

        metric = PerformanceMetric(name="test", value=75.0, timestamp=datetime.now(UTC))
        manager.evaluate_metrics([metric])
        assert len(manager.alerts) == 1

        manager.clear_alerts()
        assert not manager.alerts


class TestOperationContext:
    """Tests for OperationContext class."""

    @pytest.fixture
    def tracker(self) -> PerformanceTracker:
        """Create a PerformanceTracker instance."""
        return PerformanceTracker()

    def test_context_manager_records_duration(self, tracker: PerformanceTracker) -> None:
        """Test context manager records operation duration."""
        with OperationContext(tracker, "test_operation"):
            pass

        assert len(tracker.metrics) >= 1
        duration_metric = next(
            (m for m in tracker.metrics if "operation_duration" in m.name),
            None,
        )
        assert duration_metric is not None

    def test_context_manager_records_success(self, tracker: PerformanceTracker) -> None:
        """Test context manager records success counter."""
        with OperationContext(tracker, "test_op"):
            pass

        success_counter = tracker.counters.get("operation_success_test_op", 0)
        assert success_counter == 1

    def test_context_manager_records_error(self, tracker: PerformanceTracker) -> None:
        """Test context manager records error counter on exception."""
        with pytest.raises(ValueError), OperationContext(tracker, "failing_op"):
            raise ValueError("Test error")

        error_counter = tracker.counters.get("operation_errors_failing_op", 0)
        assert error_counter == 1

    def test_context_manager_with_tags(self, tracker: PerformanceTracker) -> None:
        """Test context manager passes tags."""
        with OperationContext(tracker, "tagged_op", tags={"env": "test"}):
            pass

        # Tags are passed to end_operation and record_counter
        assert len(tracker.metrics) >= 1


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    @pytest.fixture
    def collector(self) -> MetricsCollector:
        """Create a MetricsCollector instance."""
        return MetricsCollector()

    def test_init_creates_components(self, collector: MetricsCollector) -> None:
        """Test collector initializes with all components."""
        assert collector.performance_tracker is not None
        assert collector.resource_monitor is not None
        assert collector.alert_manager is not None
        assert collector.running is False

    def test_init_sets_default_alerts(self, collector: MetricsCollector) -> None:
        """Test collector sets up default alert rules."""
        # Should have CPU, Memory, and Disk alerts
        assert len(collector.alert_manager.rules) >= 5

    def test_record_operation_returns_context(self, collector: MetricsCollector) -> None:
        """Test record_operation returns OperationContext."""
        ctx = collector.record_operation("test_op")
        assert isinstance(ctx, OperationContext)

    def test_record_counter(self, collector: MetricsCollector) -> None:
        """Test recording counter through collector."""
        collector.record_counter("test_counter", increment=5)
        assert collector.performance_tracker.counters["test_counter"] == 5

    def test_record_gauge(self, collector: MetricsCollector) -> None:
        """Test recording gauge through collector."""
        collector.record_gauge("test_gauge", 42.0, unit="items")
        assert len(collector.performance_tracker.metrics) == 1
        assert collector.performance_tracker.metrics[0].value == 42.0

    def test_add_alert_rule(self, collector: MetricsCollector) -> None:
        """Test adding custom alert rule."""
        initial_rules = len(collector.alert_manager.rules)
        rule = ThresholdRule("Custom", 50.0, AlertLevel.INFO)
        collector.add_alert_rule(rule)

        assert len(collector.alert_manager.rules) == initial_rules + 1

    def test_add_alert_handler(self, collector: MetricsCollector) -> None:
        """Test adding alert handler."""
        handler = MagicMock()
        collector.add_alert_handler(handler)

        assert len(collector.alert_manager.handlers) == 1

    def test_get_summary_structure(self, collector: MetricsCollector) -> None:
        """Test get_summary returns expected structure."""
        summary = collector.get_summary()

        assert "timestamp" in summary
        assert "monitoring_status" in summary
        assert "alerts" in summary
        assert "performance" in summary
        assert "resources" in summary
        assert summary["monitoring_status"] == "stopped"

    @pytest.mark.asyncio
    async def test_start_monitoring_sets_running(self, collector: MetricsCollector) -> None:
        """Test start_monitoring sets running flag."""
        collector.start_monitoring(interval=60.0)

        assert collector.running is True
        assert collector.collection_interval == 60.0
        assert collector.collection_task is not None

        # Proper async cleanup
        await collector.stop_monitoring()

    @pytest.mark.asyncio
    async def test_stop_monitoring(self, collector: MetricsCollector) -> None:
        """Test stop_monitoring cancels collection task."""
        collector.start_monitoring(interval=60.0)
        assert collector.running is True

        await collector.stop_monitoring()

        assert collector.running is False
