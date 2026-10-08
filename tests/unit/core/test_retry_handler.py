"""Tests for retry handler with exponential backoff."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta, tzinfo
from unittest.mock import AsyncMock, call

import pytest

from core.retry_handler import (
    DatabaseRetryHandler,
    RetryOperationContext,
    RetryPolicy,
)


class SteppingClock:
    """Stand-in for the retry handler's datetime that moves only when a test advances it."""

    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self, _tz: tzinfo | None = None) -> datetime:
        """Return the current fake time."""
        return self.current

    def advance(self, seconds: float) -> None:
        """Move the fake time forward."""
        self.current += timedelta(seconds=seconds)


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test.retry")


@pytest.fixture
def retry_handler(logger: logging.Logger) -> DatabaseRetryHandler:
    """Create a DatabaseRetryHandler instance."""
    return DatabaseRetryHandler(logger)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> SteppingClock:
    """Replace the clock the retry handler reads, so a test decides when the deadline passes."""
    stepping_clock = SteppingClock()
    monkeypatch.setattr("core.retry_handler.datetime", stepping_clock)
    return stepping_clock


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
        context = RetryOperationContext(policy=policy)

        assert context.policy is policy
        assert isinstance(context.start_time, datetime)

    def test_total_elapsed_seconds(self) -> None:
        """Test elapsed time calculation."""
        policy = RetryPolicy()
        context = RetryOperationContext(policy=policy)

        # Should be very small (just created)
        assert context.total_elapsed_seconds >= 0
        assert context.total_elapsed_seconds < 1

    def test_has_exceeded_timeout_false(self) -> None:
        """Test timeout not exceeded."""
        policy = RetryPolicy(operation_timeout_seconds=300.0)
        context = RetryOperationContext(policy=policy)

        assert context.has_exceeded_timeout is False

    def test_has_exceeded_timeout_true(self) -> None:
        """Test timeout exceeded."""
        policy = RetryPolicy(operation_timeout_seconds=0.001)
        # Set start time in the past
        context = RetryOperationContext(
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

    def test_any_os_error_is_transient(self, retry_handler: DatabaseRetryHandler) -> None:
        """Every OSError counts as transient, whatever its errno, even when its message matches no transient pattern."""
        error = OSError(2, "No such file or directory")
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


class TestExecuteWithRetry:
    """Tests for execute_with_retry, the handler's retry API."""

    @pytest.mark.asyncio
    async def test_successful_execution(self, retry_handler: DatabaseRetryHandler) -> None:
        """Test successful execution returns result."""

        async def operation() -> str:
            """Test operation that returns success."""
            return "success"

        result = await retry_handler.execute_with_retry(operation, "test_op")
        assert result == "success"

    @pytest.mark.parametrize(
        "transient_error",
        [
            pytest.param(OSError("connection reset"), id="OSError"),
            pytest.param(ValueError("database is locked"), id="ValueError"),
            pytest.param(RuntimeError("deadlock detected"), id="RuntimeError"),
        ],
    )
    @pytest.mark.asyncio
    async def test_transient_error_is_retried_after_backoff_delay(
        self,
        retry_handler: DatabaseRetryHandler,
        monkeypatch: pytest.MonkeyPatch,
        transient_error: Exception,
    ) -> None:
        """A transient ValueError, RuntimeError or OSError is retried after the backoff delay for each attempt."""
        policy = RetryPolicy(max_retries=2)
        sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", sleep)
        operation = AsyncMock(side_effect=[transient_error, transient_error, "success"])

        result = await retry_handler.execute_with_retry(operation, "test_op", policy)

        assert result == "success"
        assert operation.await_count == 3
        assert sleep.await_args_list == [call(DatabaseRetryHandler.calculate_delay_seconds(attempt, policy)) for attempt in (0, 1)]

    @pytest.mark.asyncio
    async def test_last_attempt_error_is_reraised_unchanged(self, retry_handler: DatabaseRetryHandler) -> None:
        """When every attempt fails, the error of the last allowed attempt is re-raised as it was."""
        last_error = OSError("connection reset")
        operation = AsyncMock(side_effect=[OSError("connection reset"), OSError("connection reset"), last_error])
        policy = RetryPolicy(max_retries=2, base_delay_seconds=0.0)

        with pytest.raises(OSError, match="connection reset") as raised:
            await retry_handler.execute_with_retry(operation, "test_op", policy)

        assert raised.value is last_error
        assert operation.await_count == 3

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(ValueError("invalid value"), id="non-transient"),
            pytest.param(LookupError("connection reset"), id="type-never-retried"),
        ],
    )
    @pytest.mark.asyncio
    async def test_error_propagates_without_retry(self, retry_handler: DatabaseRetryHandler, error: Exception) -> None:
        """A non-transient error, or an exception other than ValueError, RuntimeError and OSError, propagates after one call."""
        operation = AsyncMock(side_effect=error)
        policy = RetryPolicy(max_retries=2, base_delay_seconds=0.0)

        with pytest.raises(type(error), match=str(error)) as raised:
            await retry_handler.execute_with_retry(operation, "test_op", policy)

        assert raised.value is error
        assert operation.await_count == 1

    @pytest.mark.parametrize(
        "elapsed_at_failure",
        [
            pytest.param(11.0, id="deadline-passed"),
            pytest.param(9.5, id="backoff-would-pass-deadline"),
        ],
    )
    @pytest.mark.asyncio
    async def test_timeout_replaces_next_attempt(
        self,
        *,
        retry_handler: DatabaseRetryHandler,
        clock: SteppingClock,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        elapsed_at_failure: float,
    ) -> None:
        """When the next attempt could only start past the deadline, TimeoutError is raised at once, caused by the last failure."""
        sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", sleep)
        error = OSError("connection reset")

        async def transient_failure() -> str:
            """Fail transiently once the given time has passed."""
            clock.advance(elapsed_at_failure)
            raise error

        operation = AsyncMock(side_effect=transient_failure)
        # A one-second backoff without jitter, so 9.5s plus the backoff ends past the 10s deadline
        policy = RetryPolicy(max_retries=2, base_delay_seconds=1.0, jitter_range=0.0, operation_timeout_seconds=10.0)

        with caplog.at_level(logging.ERROR, logger="test.retry"), pytest.raises(TimeoutError, match="no time left") as raised:
            await retry_handler.execute_with_retry(operation, "test_op", policy)

        assert raised.value.__cause__ is error
        assert operation.await_count == 1
        sleep.assert_not_awaited()
        # Callers may drop the TimeoutError, so the log line names the failure behind it
        assert any("connection reset" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_late_backoff_raises_timeout_before_the_next_attempt(
        self,
        *,
        retry_handler: DatabaseRetryHandler,
        clock: SteppingClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A backoff that ends late, past the deadline, is followed by TimeoutError instead of the next attempt."""
        error = OSError("connection reset")

        async def oversleep(seconds: float) -> None:
            """Sleep a second longer than asked, as a stalled event loop or a suspended laptop would."""
            clock.advance(seconds + 1.0)

        sleep = AsyncMock(side_effect=oversleep)
        monkeypatch.setattr("asyncio.sleep", sleep)

        async def transient_failure() -> str:
            """Fail transiently with time left for the backoff as planned."""
            clock.advance(8.5)
            raise error

        operation = AsyncMock(side_effect=transient_failure)
        policy = RetryPolicy(max_retries=2, base_delay_seconds=1.0, jitter_range=0.0, operation_timeout_seconds=10.0)

        with pytest.raises(TimeoutError, match="no time left") as raised:
            await retry_handler.execute_with_retry(operation, "test_op", policy)

        assert raised.value.__cause__ is error
        assert operation.await_count == 1
        assert sleep.await_args_list == [call(1.0)]

    @pytest.mark.parametrize(
        "elapsed_at_failure",
        [
            pytest.param(8.5, id="ends-before-deadline"),
            pytest.param(9.0, id="ends-at-deadline"),
        ],
    )
    @pytest.mark.asyncio
    async def test_retry_that_fits_before_the_deadline_runs(
        self,
        *,
        retry_handler: DatabaseRetryHandler,
        clock: SteppingClock,
        monkeypatch: pytest.MonkeyPatch,
        elapsed_at_failure: float,
    ) -> None:
        """A backoff that ends by the deadline is waited out, and the retry runs."""
        sleep = AsyncMock(side_effect=clock.advance)
        monkeypatch.setattr("asyncio.sleep", sleep)
        failed_attempts: list[str] = []

        async def fail_once() -> str:
            """Fail transiently on the first attempt and succeed on the next."""
            if not failed_attempts:
                failed_attempts.append("connection reset")
                clock.advance(elapsed_at_failure)
                raise OSError("connection reset")
            return "success"

        operation = AsyncMock(side_effect=fail_once)
        policy = RetryPolicy(max_retries=2, base_delay_seconds=1.0, jitter_range=0.0, operation_timeout_seconds=10.0)

        result = await retry_handler.execute_with_retry(operation, "test_op", policy)

        assert result == "success"
        assert operation.await_count == 2
        assert sleep.await_args_list == [call(1.0)]

    @pytest.mark.asyncio
    async def test_negative_max_retries_runs_the_operation_once(
        self,
        *,
        retry_handler: DatabaseRetryHandler,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A negative retry count means no retries, not no attempt: the operation runs once, and the log counts one attempt."""
        operation = AsyncMock(return_value="success")

        with caplog.at_level(logging.DEBUG, logger="test.retry"):
            result = await retry_handler.execute_with_retry(operation, "test_op", RetryPolicy(max_retries=-1))

        assert result == "success"
        operation.assert_awaited_once()
        assert any("succeeded on attempt 1/1" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_negative_max_retries_does_not_retry_a_failure(
        self,
        *,
        retry_handler: DatabaseRetryHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A negative retry count allows no retry: a transient failure is raised as is, with no backoff."""
        sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", sleep)
        error = OSError("connection reset")
        operation = AsyncMock(side_effect=error)

        with pytest.raises(OSError, match="connection reset") as raised:
            await retry_handler.execute_with_retry(operation, "test_op", RetryPolicy(max_retries=-1))

        assert raised.value is error
        operation.assert_awaited_once()
        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_running_attempt_keeps_result_after_deadline(self, retry_handler: DatabaseRetryHandler, clock: SteppingClock) -> None:
        """An attempt that outlasts the deadline is not interrupted and still returns its result."""

        async def outlasting_attempt() -> str:
            """Run past the deadline on both the handler's clock and the event loop's."""
            clock.advance(11)
            await asyncio.sleep(0.05)
            return "late success"

        result = await retry_handler.execute_with_retry(outlasting_attempt, "test_op", RetryPolicy(operation_timeout_seconds=0.01))

        assert result == "late success"

    @pytest.mark.parametrize(
        ("error", "max_retries"),
        [
            pytest.param(ValueError("invalid value"), 3, id="non-transient"),
            pytest.param(OSError("connection reset"), 0, id="transient-on-last-attempt"),
        ],
    )
    @pytest.mark.asyncio
    async def test_running_attempt_keeps_error_after_deadline(
        self,
        retry_handler: DatabaseRetryHandler,
        clock: SteppingClock,
        error: Exception,
        max_retries: int,
    ) -> None:
        """An error that ends the loop comes through unchanged even after the deadline."""

        async def slow_failure() -> str:
            """Fail after the deadline has passed."""
            clock.advance(11)
            raise error

        policy = RetryPolicy(max_retries=max_retries, operation_timeout_seconds=10.0)
        with pytest.raises(type(error), match=str(error)) as raised:
            await retry_handler.execute_with_retry(slow_failure, "test_op", policy)

        assert raised.value is error


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
