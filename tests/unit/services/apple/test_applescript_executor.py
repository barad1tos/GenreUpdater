"""Smoke tests for AppleScriptExecutor (#219).

Tests the error class and pure logging methods.
Subprocess execution is NOT tested here — mocking subprocess
gives false confidence and is better covered by integration tests.
"""

from __future__ import annotations

import errno
import logging

import pytest

from core.apple_script_names import (
    FETCH_TRACK_IDS,
    FETCH_TRACKS,
    UPDATE_PROPERTY,
)
from core.tracks.track_delta import FIELD_SEPARATOR, LINE_SEPARATOR
from services.apple.applescript_executor import (
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


# AppleScriptExecutor — pure methods only

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
        import asyncio
        sem = asyncio.Semaphore(2)
        executor.update_semaphore(sem)
        assert executor.semaphore is sem


class TestUpdateRateLimiter:

    def test_updates_rate_limiter(self, executor: AppleScriptExecutor) -> None:
        from unittest.mock import MagicMock
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
