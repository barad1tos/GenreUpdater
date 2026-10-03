"""Exit code tests for the main entry point."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from app.music_updater import LibraryFetchError


@pytest.mark.asyncio
async def test_library_fetch_error_exits_non_zero() -> None:
    """A failed library fetch must exit non-zero so the daemon wrapper reports an error."""
    environment = (MagicMock(), None, MagicMock(), MagicMock())
    with (
        patch.object(main, "CLI"),
        patch.object(main, "_setup_environment", AsyncMock(return_value=environment)),
        patch.object(main, "_generate_analytics_report"),
        patch.object(main, "_cleanup_resources", AsyncMock()),
        patch.object(main.Orchestrator, "run_command", AsyncMock(side_effect=LibraryFetchError("no tracks"))),
        pytest.raises(SystemExit) as exit_info,
    ):
        await main.main_async()

    assert exit_info.value.code == 1
