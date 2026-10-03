"""Exit code tests for the main entry point."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from app.orchestrator import MusicAppNotRunningError


@pytest.mark.asyncio
async def test_music_app_not_running_exits_with_tempfail() -> None:
    """A run skipped because Music.app is closed exits with EX_TEMPFAIL, not as a critical error."""
    logger_error = MagicMock()
    environment = (MagicMock(), None, MagicMock(), logger_error)
    with (
        patch.object(main, "CLI"),
        patch.object(main, "_setup_environment", AsyncMock(return_value=environment)),
        patch.object(main, "_generate_analytics_report"),
        patch.object(main, "_cleanup_resources", AsyncMock()) as cleanup_mock,
        patch.object(main.Orchestrator, "run_command", AsyncMock(side_effect=MusicAppNotRunningError("default"))),
        pytest.raises(SystemExit) as exit_info,
    ):
        await main.main_async()

    assert exit_info.value.code == os.EX_TEMPFAIL
    logger_error.critical.assert_not_called()
    cleanup_mock.assert_awaited_once()
