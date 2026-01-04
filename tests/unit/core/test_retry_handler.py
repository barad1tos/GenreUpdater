"""Tests for retry handler with exponential backoff."""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from core.retry_handler import (
    DatabaseRetryHandler,
    RetryOperationContext,
    RetryPolicy,
)


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test.retry")


@pytest.fixture
def retry_handler(logger: logging.Logger) -> DatabaseRetryHandler:
    """Create a DatabaseRetryHandler instance."""
    return DatabaseRetryHandler(logger)


class TestRetryPolicy:
    """Tests for RetryPolicy dataclass."""

    def test_default_values(self) -> None:
        """Test default policy values."""
        policy = RetryPolicy()

        assert policy.max_retries == 3
        assert policy.base_delay_seconds == 1.0
        assert policy.max_delay_seconds == 60.0
        assert policy.exponential_base == 2.0
        assert policy.jitter_range == 0.1
        assert policy.operation_timeout_seconds == 300.0

    def test_custom_values(self) -> None:
        """Test custom policy values."""
        policy = RetryPolicy(
            max_retries=5,
            base_delay_seconds=0.5,
            max_delay_seconds=30.0,
            exponential_base=3.0,
            jitter_range=0.2,
            operation_timeout_seconds=600.0,
        )

        assert policy.max_retries == 5
        assert policy.base_delay_seconds == 0.5
        assert policy.max_delay_seconds == 30.0
        assert policy.exponential_base == 3.0
        assert policy.jitter_range == 0.2
        assert policy.operation_timeout_seconds == 600.0


class TestRetryOperationContext:
    """Tests for RetryOperationContext dataclass."""

    def test_creation(self) -> None:
        """Test context creation."""
        policy = RetryPolicy()
        context = RetryOperationContext(operation_id="test_op", policy=policy)

        assert context.operation_id == "test_op"
        assert context.attempt_count == 0
        assert context.last_error is None
        assert isinstance(context.start_time, datetime)

    def test_total_elapsed_seconds(self) -> None:
        """Test elapsed time calculation."""
        policy = RetryPolicy()
        context = RetryOperationContext(operation_id="test_op", policy=policy)

        # Should be very small (just created)
        assert context.total_elapsed_seconds >= 0
        assert context.total_elapsed_seconds < 1

    def test_has_exceeded_timeout_false(self) -> None:
        """Test timeout not exceeded."""
        policy = RetryPolicy(operation_timeout_seconds=300.0)
        context = RetryOperationContext(operation_id="test_op", policy=policy)

        assert context.has_exceeded_timeout is False

    def test_has_exceeded_timeout_true(self) -> None:
        """Test timeout exceeded."""
        policy = RetryPolicy(operation_timeout_seconds=0.001)
        # Set start time in the past
        context = RetryOperationContext(
            operation_id="test_op",
            policy=policy,
            start_time=datetime.now(UTC) - timedelta(seconds=10),
        )

        assert context.has_exceeded_timeout is True


class TestIsTransientError:
    """Tests for transient error detection."""

    def test_connection_error_is_transient(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test ConnectionError is transient."""
        error = ConnectionError("Connection refused")
        assert retry_handler.is_transient_error(error) is True

    def test_timeout_error_is_transient(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test TimeoutError is transient."""
        error = TimeoutError("Operation timed out")
        assert retry_handler.is_transient_error(error) is True

    def test_os_error_with_transient_errno(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test OSError with transient errno."""
        error = OSError(111, "Connection refused")  # errno 111
        assert retry_handler.is_transient_error(error) is True

    def test_os_error_connection_reset(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test OSError with connection reset errno."""
        error = OSError(104, "Connection reset by peer")
        assert retry_handler.is_transient_error(error) is True

    def test_database_locked_is_transient(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test database locked error is transient."""
        error = Exception("database is locked")
        assert retry_handler.is_transient_error(error) is True

    def test_deadlock_is_transient(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test deadlock error is transient."""
        error = Exception("Deadlock detected")
        assert retry_handler.is_transient_error(error) is True

    def test_too_many_connections_is_transient(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test too many connections error is transient."""
        error = Exception("Too many connections")
        assert retry_handler.is_transient_error(error) is True

    def test_value_error_not_transient(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test ValueError is not transient."""
        error = ValueError("Invalid value")
        assert retry_handler.is_transient_error(error) is False

    def test_key_error_not_transient(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test KeyError is not transient."""
        error = KeyError("missing_key")
        assert retry_handler.is_transient_error(error) is False


class TestCalculateDelaySeconds:
    """Tests for delay calculation."""

    def test_first_attempt_base_delay(self) -> None:
        """Test first attempt uses base delay."""
        policy = RetryPolicy(base_delay_seconds=1.0, jitter_range=0.0)
        delay = DatabaseRetryHandler.calculate_delay_seconds(0, policy)

        assert delay == pytest.approx(1.0, abs=0.1)

    def test_exponential_growth(self) -> None:
        """Test delay grows exponentially."""
        policy = RetryPolicy(
            base_delay_seconds=1.0,
            exponential_base=2.0,
            max_delay_seconds=100.0,
            jitter_range=0.0,
        )

        delay_0 = DatabaseRetryHandler.calculate_delay_seconds(0, policy)
        delay_1 = DatabaseRetryHandler.calculate_delay_seconds(1, policy)
        delay_2 = DatabaseRetryHandler.calculate_delay_seconds(2, policy)

        # Should approximately double each time
        assert delay_0 == pytest.approx(1.0, abs=0.2)
        assert delay_1 == pytest.approx(2.0, abs=0.4)
        assert delay_2 == pytest.approx(4.0, abs=0.8)

    def test_max_delay_cap(self) -> None:
        """Test delay is capped at max."""
        policy = RetryPolicy(
            base_delay_seconds=1.0,
            exponential_base=10.0,
            max_delay_seconds=5.0,
            jitter_range=0.0,
        )

        # Very high attempt number
        delay = DatabaseRetryHandler.calculate_delay_seconds(10, policy)

        assert delay <= 5.5  # max + some jitter tolerance

    def test_jitter_adds_variation(self) -> None:
        """Test jitter adds variation to delays."""
        policy = RetryPolicy(
            base_delay_seconds=1.0,
            jitter_range=0.5,  # 50% jitter
        )

        delays = [DatabaseRetryHandler.calculate_delay_seconds(i, policy) for i in range(10)]

        # Delays should not all be identical due to jitter
        unique_delays = set(delays)
        # With deterministic jitter based on attempt number, we expect variation
        assert len(unique_delays) >= 5

    def test_delay_never_negative(self) -> None:
        """Test delay is never negative."""
        policy = RetryPolicy(
            base_delay_seconds=0.1,
            jitter_range=0.9,  # High jitter
        )

        for attempt in range(100):
            delay = DatabaseRetryHandler.calculate_delay_seconds(attempt, policy)
            assert delay >= 0


class TestAsyncRetryOperation:
    """Tests for async retry operation context manager."""

    @pytest.mark.asyncio
    async def test_successful_operation_no_retry(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test successful operation doesn't retry."""
        attempts = 0

        async with retry_handler.async_retry_operation("test_op") as ctx:
            attempts += 1
            ctx.metadata["table"] = "tracks"
            # Operation succeeds

        assert attempts == 1
        assert ctx.attempt_count == 1

    @pytest.mark.asyncio
    async def test_non_transient_error_no_retry(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test non-transient error doesn't retry."""
        attempts = 0
        policy = RetryPolicy(max_retries=3, base_delay_seconds=0.01)
        error_message = "Invalid value"

        async def operation_that_raises() -> None:
            """Execute operation that raises non-transient error."""
            nonlocal attempts
            async with retry_handler.async_retry_operation("test_op", policy) as _:
                attempts += 1
                raise ValueError(error_message)

        with pytest.raises(ValueError, match=error_message):
            await operation_that_raises()

        assert attempts == 1


class TestExecuteWithRetry:
    """Tests for execute_with_retry convenience method."""

    @pytest.mark.asyncio
    async def test_successful_execution(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test successful execution returns result."""

        async def operation() -> str:
            """Test operation that returns success."""
            return "success"

        result = await retry_handler.execute_with_retry(operation, "test_op")
        assert result == "success"


class TestDatabaseRetryHandlerInit:
    """Tests for DatabaseRetryHandler initialization."""

    def test_default_policy(self, logger: logging.Logger) -> None:
        """Test default policy is set."""
        handler = DatabaseRetryHandler(logger)

        assert handler.database_policy.max_retries == 5
        assert handler.database_policy.max_delay_seconds == 30.0
        assert handler.database_policy.jitter_range == 0.2

    def test_custom_policy(self, logger: logging.Logger) -> None:
        """Test custom policy is used."""
        custom_policy = RetryPolicy(max_retries=10)
        handler = DatabaseRetryHandler(logger, default_policy=custom_policy)

        assert handler.database_policy.max_retries == 10

    def test_transient_error_patterns(self, logger: logging.Logger) -> None:
        """Test transient error patterns are set."""
        handler = DatabaseRetryHandler(logger)

        assert "connection refused" in handler._transient_error_patterns
        assert "timeout" in handler._transient_error_patterns
        assert "deadlock" in handler._transient_error_patterns


class TestRetryMetadata:
    """Tests for retry metadata handling."""

    @pytest.mark.asyncio
    async def test_metadata_preserved(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test metadata is preserved across attempts."""
        async with retry_handler.async_retry_operation("test_op") as ctx:
            ctx.metadata["table"] = "tracks"
            ctx.metadata["operation_type"] = "insert"

        assert ctx.metadata["table"] == "tracks"
        assert ctx.metadata["operation_type"] == "insert"
