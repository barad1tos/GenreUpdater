"""Tests for AppleScriptExecutor (#219).

Tests the error class, pure logging methods, subprocess execution
error branches, and process cleanup logic.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.apple_script_names import (
    FETCH_TRACK_IDS,
    FETCH_TRACKS,
    UPDATE_PROPERTY,
)
from core.tracks.track_delta import FIELD_SEPARATOR, LINE_SEPARATOR
from services.apple.applescript_executor import (
    ERRNO_CONNECTION_REFUSED,
    ERRNO_CONNECTION_TIMED_OUT,
    PROCESS_EXIT_WAIT_SECONDS,
    PROCESS_KILL_WAIT_SECONDS,
    AppleScriptExecutionError,
    AppleScriptExecutor,
)


# AppleScriptExecutionError


class TestAppleScriptExecutionError:
    def test_is_os_error(self) -> None:
        err = AppleScriptExecutionError("fail", "test_script")
        assert isinstance(err, OSError)

    def test_stores_label(self) -> None:
        err = AppleScriptExecutionError("fail", "my_label")
        assert err.label == "my_label"

    def test_errno_code_propagated(self) -> None:
        err = AppleScriptExecutionError("timeout", "script", errno_code=110)
        assert err.errno == 110

    def test_no_errno_when_none(self) -> None:
        err = AppleScriptExecutionError("fail", "script", errno_code=None)
        assert err.errno is None

    def test_message_accessible(self) -> None:
        err = AppleScriptExecutionError("something broke", "label")
        assert "something broke" in str(err)

    def test_transient_errno_codes(self) -> None:
        """Verify errno codes used for transient error signaling."""
        timeout_err = AppleScriptExecutionError("t", "s", errno_code=110)
        conn_err = AppleScriptExecutionError("c", "s", errno_code=errno.ECONNREFUSED)
        assert timeout_err.errno == 110
        assert conn_err.errno == errno.ECONNREFUSED


# AppleScriptExecutor -- pure methods only


@pytest.fixture
def executor(mock_console_logger: logging.Logger, mock_error_logger: logging.Logger) -> AppleScriptExecutor:
    """Create executor with mock loggers, no semaphore."""
    return AppleScriptExecutor(
        semaphore=None,
        apple_scripts_directory="/tmp/test",
        console_logger=mock_console_logger,
        error_logger=mock_error_logger,
    )


class TestUpdateSemaphore:
    def test_updates_semaphore(self, executor: AppleScriptExecutor) -> None:
        sem = asyncio.Semaphore(2)
        executor.update_semaphore(sem)
        assert executor.semaphore is sem


class TestUpdateRateLimiter:
    def test_updates_rate_limiter(self, executor: AppleScriptExecutor) -> None:
        limiter = MagicMock()
        executor.update_rate_limiter(limiter)
        assert executor.rate_limiter is limiter


class TestLogScriptSuccess:
    def test_update_property_logs_debug(self, executor: AppleScriptExecutor) -> None:
        label = f"{UPDATE_PROPERTY} year=2020"
        executor.log_script_success(label, "OK", 0.5)
        executor.console_logger.debug.assert_called_once()

    def test_track_data_logs_track_count(self, executor: AppleScriptExecutor) -> None:
        label = FETCH_TRACKS
        # 2 line separators = 2 tracks
        result = f"data1{LINE_SEPARATOR}data2{LINE_SEPARATOR}"
        executor.log_script_success(label, result, 1.0)
        executor.console_logger.info.assert_called_once()
        call_args = executor.console_logger.info.call_args
        assert "2 tracks" in (call_args[0][0] % call_args[0][1:])

    def test_fetch_ids_logs_id_count(self, executor: AppleScriptExecutor) -> None:
        label = FETCH_TRACK_IDS
        result = "1,2,3,4,5"  # 5 IDs
        executor.log_script_success(label, result, 0.3)
        executor.console_logger.info.assert_called_once()
        call_args = executor.console_logger.info.call_args
        assert "5 IDs" in (call_args[0][0] % call_args[0][1:])

    def test_separator_result_logs_record_count(self, executor: AppleScriptExecutor) -> None:
        label = "custom_script"
        result = f"a{FIELD_SEPARATOR}b{FIELD_SEPARATOR}c"
        executor.log_script_success(label, result, 0.2)
        executor.console_logger.info.assert_called_once()

    def test_short_result_logs_full_preview(self, executor: AppleScriptExecutor) -> None:
        label = "simple_script"
        executor.log_script_success(label, "short result", 0.1)
        executor.console_logger.info.assert_called_once()

    def test_no_change_result_logs_debug(self, executor: AppleScriptExecutor) -> None:
        label = "check_script"
        executor.log_script_success(label, "No Change", 0.1)
        executor.console_logger.debug.assert_called()

    def test_empty_fetch_ids_shows_zero(self, executor: AppleScriptExecutor) -> None:
        label = FETCH_TRACK_IDS
        executor.log_script_success(label, "   ", 0.1)
        call_args = executor.console_logger.info.call_args
        assert "0 IDs" in (call_args[0][0] % call_args[0][1:])


# Module-level constants


class TestModuleConstants:
    """Verify module constants are importable and have expected values."""

    def test_process_exit_wait_seconds(self) -> None:
        assert PROCESS_EXIT_WAIT_SECONDS == 0.5

    def test_process_kill_wait_seconds(self) -> None:
        assert PROCESS_KILL_WAIT_SECONDS == 5.0

    def test_errno_connection_refused(self) -> None:
        assert ERRNO_CONNECTION_REFUSED == 61

    def test_errno_connection_timed_out(self) -> None:
        assert ERRNO_CONNECTION_TIMED_OUT == 110


# _execute_subprocess error branches


def _make_mock_process(
    returncode: int = 0,
    stdout: bytes = b"output",
    stderr: bytes = b"",
) -> AsyncMock:
    """Create a mock asyncio subprocess process.

    Args:
        returncode: Exit code to return
        stdout: Stdout bytes to return from communicate
        stderr: Stderr bytes to return from communicate
    """
    mock_proc = AsyncMock()
    mock_proc.returncode = returncode
    mock_proc.communicate = AsyncMock(return_value=(stdout, stderr))
    mock_proc.wait = AsyncMock()
    mock_proc.kill = MagicMock()
    return mock_proc


class TestExecuteSubprocessSuccess:
    """Test the successful execution path in _execute_subprocess."""

    @pytest.mark.asyncio
    async def test_returns_stdout_on_success(self, executor: AppleScriptExecutor) -> None:
        """Successful execution returns decoded stdout."""
        mock_proc = _make_mock_process(returncode=0, stdout=b"hello world")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor._execute_subprocess(["osascript"], "test_label", 30.0)

        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_logs_success(self, executor: AppleScriptExecutor) -> None:
        """Successful execution calls log_script_success."""
        mock_proc = _make_mock_process(returncode=0, stdout=b"ok")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await executor._execute_subprocess(["osascript"], "simple_script", 30.0)

        executor.console_logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_calls_cleanup_on_success(self, executor: AppleScriptExecutor) -> None:
        """Finally block calls cleanup_process even on success."""
        mock_proc = _make_mock_process(returncode=0, stdout=b"ok")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await executor._execute_subprocess(["osascript"], "test_label", 30.0)

        # cleanup_process calls proc.wait()
        mock_proc.wait.assert_called()


class TestExecuteSubprocessStderr:
    """Test stderr handling in _execute_subprocess."""

    @pytest.mark.asyncio
    async def test_stderr_logged_as_warning(self, executor: AppleScriptExecutor) -> None:
        """Stderr output is logged as a warning."""
        mock_proc = _make_mock_process(returncode=0, stdout=b"ok", stderr=b"some warning")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await executor._execute_subprocess(["osascript"], "test_label", 30.0)

        assert result == "ok"
        executor.console_logger.warning.assert_called()
        warning_args = executor.console_logger.warning.call_args[0]
        assert "stderr" in warning_args[0]
        assert "some warning" in str(warning_args)


class TestExecuteSubprocessNonZeroReturn:
    """Test non-zero return code branch in _execute_subprocess."""

    @pytest.mark.asyncio
    async def test_raises_execution_error_with_errno(self, executor: AppleScriptExecutor) -> None:
        """Non-zero return code raises AppleScriptExecutionError with ERRNO_CONNECTION_REFUSED."""
        mock_proc = _make_mock_process(returncode=1, stderr=b"Music got an error")

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError) as exc_info,
        ):
            await executor._execute_subprocess(["osascript"], "failing_script", 30.0)

        assert exc_info.value.errno == ERRNO_CONNECTION_REFUSED
        assert exc_info.value.label == "failing_script"
        assert "Music got an error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_zero_without_stderr_uses_return_code(self, executor: AppleScriptExecutor) -> None:
        """Non-zero return code with empty stderr uses return code in message."""
        mock_proc = _make_mock_process(returncode=2, stderr=b"")

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError) as exc_info,
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

        assert "return code 2" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_zero_logs_error(self, executor: AppleScriptExecutor) -> None:
        """Non-zero return code logs to error_logger."""
        mock_proc = _make_mock_process(returncode=1, stderr=b"err")

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError),
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

        executor.error_logger.error.assert_called()


class TestExecuteSubprocessTimeout:
    """Test TimeoutError branch in _execute_subprocess."""

    @pytest.mark.asyncio
    async def test_timeout_raises_with_errno(self, executor: AppleScriptExecutor) -> None:
        """TimeoutError raises AppleScriptExecutionError with ERRNO_CONNECTION_TIMED_OUT."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError) as exc_info,
        ):
            await executor._execute_subprocess(["osascript"], "slow_script", 5.0)

        assert exc_info.value.errno == ERRNO_CONNECTION_TIMED_OUT
        assert exc_info.value.label == "slow_script"
        assert "timeout" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_timeout_logs_exception(self, executor: AppleScriptExecutor) -> None:
        """TimeoutError is logged via error_logger.exception."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError),
        ):
            await executor._execute_subprocess(["osascript"], "script", 10.0)

        executor.error_logger.exception.assert_called()


class TestExecuteSubprocessOSError:
    """Test subprocess.SubprocessError and OSError re-raise branch."""

    @pytest.mark.asyncio
    async def test_subprocess_error_reraised(self, executor: AppleScriptExecutor) -> None:
        """subprocess.SubprocessError is re-raised directly."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(
            side_effect=subprocess.SubprocessError("pipe broken"),
        )

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(subprocess.SubprocessError, match="pipe broken"),
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

    @pytest.mark.asyncio
    async def test_os_error_reraised(self, executor: AppleScriptExecutor) -> None:
        """OSError is re-raised directly for retry handler detection."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=OSError("no such file"))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(OSError, match="no such file"),
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

    @pytest.mark.asyncio
    async def test_os_error_logs_exception(self, executor: AppleScriptExecutor) -> None:
        """OSError is logged via error_logger.exception."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=OSError("disk error"))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(OSError),
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

        executor.error_logger.exception.assert_called()


class TestExecuteSubprocessCancelled:
    """Test asyncio.CancelledError re-raise branch."""

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised(self, executor: AppleScriptExecutor) -> None:
        """CancelledError is re-raised to propagate cancellation."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(asyncio.CancelledError),
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

    @pytest.mark.asyncio
    async def test_cancelled_logs_info(self, executor: AppleScriptExecutor) -> None:
        """CancelledError is logged at info level."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(asyncio.CancelledError),
        ):
            await executor._execute_subprocess(["osascript"], "cancelled_script", 30.0)

        executor.console_logger.info.assert_called()
        info_args = executor.console_logger.info.call_args[0]
        assert "cancelled" in info_args[0].lower()


class TestExecuteSubprocessUnexpectedErrors:
    """Test UnicodeDecodeError, MemoryError, RuntimeError branch."""

    @pytest.mark.asyncio
    async def test_unicode_decode_error_raises_without_errno(self, executor: AppleScriptExecutor) -> None:
        """UnicodeDecodeError wraps in AppleScriptExecutionError without errno."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(
            side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
        )

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError) as exc_info,
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

        assert exc_info.value.errno is None

    @pytest.mark.asyncio
    async def test_memory_error_raises_without_errno(self, executor: AppleScriptExecutor) -> None:
        """MemoryError wraps in AppleScriptExecutionError without errno."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=MemoryError("out of memory"))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError) as exc_info,
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

        assert exc_info.value.errno is None

    @pytest.mark.asyncio
    async def test_runtime_error_raises_without_errno(self, executor: AppleScriptExecutor) -> None:
        """RuntimeError wraps in AppleScriptExecutionError without errno."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=RuntimeError("event loop closed"))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError) as exc_info,
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

        assert exc_info.value.errno is None
        assert "event loop closed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_unexpected_error_logs_exception(self, executor: AppleScriptExecutor) -> None:
        """Unexpected errors are logged via error_logger.exception."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError),
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

        executor.error_logger.exception.assert_called()


class TestExecuteSubprocessFinallyCleanup:
    """Test that the finally block always calls cleanup_process."""

    @pytest.mark.asyncio
    async def test_cleanup_called_on_error(self, executor: AppleScriptExecutor) -> None:
        """cleanup_process is called even when an exception is raised."""
        mock_proc = _make_mock_process(returncode=1, stderr=b"err")

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError),
        ):
            await executor._execute_subprocess(["osascript"], "script", 30.0)

        mock_proc.wait.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_called_on_timeout(self, executor: AppleScriptExecutor) -> None:
        """cleanup_process is called after timeout."""
        mock_proc = _make_mock_process()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(AppleScriptExecutionError),
        ):
            await executor._execute_subprocess(["osascript"], "script", 5.0)

        mock_proc.wait.assert_called()


# cleanup_process


class TestCleanupProcess:
    """Test cleanup_process method branches."""

    @pytest.mark.asyncio
    async def test_natural_exit(self, executor: AppleScriptExecutor) -> None:
        """Process exits naturally within PROCESS_EXIT_WAIT_SECONDS."""
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        await executor.cleanup_process(mock_proc, "test_label")

        mock_proc.wait.assert_called()
        mock_proc.kill.assert_not_called()
        executor.console_logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_timeout_triggers_kill(self, executor: AppleScriptExecutor) -> None:
        """When wait times out, process is killed and waited on again."""
        mock_proc = AsyncMock()
        # First wait() raises TimeoutError, second wait() (after kill) succeeds
        mock_proc.wait = AsyncMock(side_effect=[TimeoutError(), None])
        mock_proc.kill = MagicMock()

        await executor.cleanup_process(mock_proc, "stuck_process")

        mock_proc.kill.assert_called_once()
        assert mock_proc.wait.call_count == 2
        executor.console_logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_process_lookup_error_during_kill(self, executor: AppleScriptExecutor) -> None:
        """ProcessLookupError during kill logs a warning."""
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(side_effect=TimeoutError())
        mock_proc.kill = MagicMock(side_effect=ProcessLookupError("already dead"))

        await executor.cleanup_process(mock_proc, "dead_process")

        executor.console_logger.warning.assert_called()
        warning_args = executor.console_logger.warning.call_args[0]
        assert "dead_process" in str(warning_args)

    @pytest.mark.asyncio
    async def test_timeout_after_kill_logs_warning(self, executor: AppleScriptExecutor) -> None:
        """TimeoutError after kill also logs a warning."""
        mock_proc = AsyncMock()
        # First wait times out, kill succeeds, second wait also times out
        mock_proc.wait = AsyncMock(side_effect=[TimeoutError(), TimeoutError()])
        mock_proc.kill = MagicMock()

        await executor.cleanup_process(mock_proc, "zombie_process")

        mock_proc.kill.assert_called_once()
        executor.console_logger.warning.assert_called()
