"""lock_year and the year step must agree on the lock's key: a lock the command writes is the lock the year step restores."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.year_update import YearUpdateService
from core.models.track_models import TrackDict
from services.year_locks import YearLockService
from tests.factories import create_test_app_config
from tests.unit.core.tracks.conftest import create_year_batch_processor, create_year_determinator_mock

if TYPE_CHECKING:
    from pathlib import Path

COLLABORATION_TRACK = TrackDict(id="1", name="T", artist="In Flames feat. Someone", album="Battles", album_artist="In Flames", year="2019")


@pytest.mark.asyncio
async def test_a_lock_the_command_writes_is_the_lock_the_year_step_restores(tmp_path: Path) -> None:
    config = create_test_app_config(logs_base_dir=str(tmp_path))
    locks = YearLockService(config, logging.getLogger("test.console"), logging.getLogger("test.error"))
    await locks.initialize()
    track_processor = MagicMock()
    track_processor.fetch_tracks_async = AsyncMock(return_value=[COLLABORATION_TRACK])
    retriever = MagicMock()
    retriever.write_album_year = AsyncMock(return_value=([], []))
    command = YearUpdateService(
        track_processor=track_processor,
        year_retriever=retriever,
        snapshot_manager=MagicMock(),
        config=config,
        console_logger=logging.getLogger("test.console"),
        error_logger=logging.getLogger("test.error"),
        year_locks=locks,
        dry_run=False,
    )
    determinator = create_year_determinator_mock()
    year_step = create_year_batch_processor(year_determinator=determinator, year_locks=locks)
    writer = AsyncMock()
    year_step._track_updater.update_tracks_for_album = writer
    albums = year_step.group_tracks_by_album([COLLABORATION_TRACK])

    # The person types the album artist; the lock lands under the key the year step groups by
    await command.run_lock_year("In Flames", "Battles", "1996")
    await year_step.process_albums_in_batches(albums, [], [])

    call = writer.await_args
    assert call is not None
    assert (call.kwargs["year"], call.kwargs["reason"]) == ("1996", "lock_restore")
    determinator.should_skip_album.assert_not_awaited()

    # Unlocked, the album takes the normal path again
    await command.run_unlock_year("In Flames", "Battles")
    writer.reset_mock()
    await year_step.process_albums_in_batches(albums, [], [])

    assert locks.all_locks() == []
    determinator.should_skip_album.assert_awaited_once()
