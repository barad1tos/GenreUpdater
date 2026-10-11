"""A locked album is restored and never queried; an outside change is judged by the providers, the person, then written."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.cache_types import LockSource
from core.models.protocols import YearLookupUnavailableError
from core.tracks.year_drift import DriftDecision
from tests.unit.core.tracks.conftest import create_test_track, create_year_batch_processor, create_year_determinator_mock

if TYPE_CHECKING:
    from core.models.track_models import TrackDict
    from core.tracks.year_batch import YearBatchProcessor


def _album(*years: str, set_by_mgu: str | None = None) -> list[TrackDict]:
    return [create_test_track(str(i + 1), artist="In Flames", album="Battles", year=year, year_set_by_mgu=set_by_mgu) for i, year in enumerate(years)]


def _processor(
    *,
    locked: str | None = None,
    provider: str | None = "2016",
    decision: DriftDecision = DriftDecision.APPLY,
    provider_error: Exception | None = None,
) -> tuple[YearBatchProcessor, MagicMock, MagicMock, MagicMock, AsyncMock]:
    determinator = create_year_determinator_mock()
    determinator.provider_year = AsyncMock(return_value=provider, side_effect=provider_error)
    locks = MagicMock()
    locks.get_locked_year = MagicMock(return_value=locked)
    locks.lock = AsyncMock()
    decider = MagicMock()
    decider.decide = AsyncMock(return_value=decision)
    processor = create_year_batch_processor(year_determinator=determinator, year_locks=locks, drift_decider=decider)
    writer = AsyncMock()
    processor._track_updater.update_tracks_for_album = writer
    return processor, determinator, locks, decider, writer


async def _run(processor: YearBatchProcessor, tracks: list[TrackDict], previous_years: dict[str, str] | None = None) -> None:
    await processor._process_single_album(
        "In Flames", "Battles", album_tracks=tracks, updated_tracks=[], changes_log=[], previous_years=previous_years
    )


def _write_kwargs(writer: AsyncMock) -> dict[str, object]:
    call = writer.await_args
    assert call is not None
    return dict(call.kwargs)


@pytest.mark.asyncio
async def test_a_locked_album_is_restored_and_never_queried() -> None:
    processor, determinator, _, _, writer = _processor(locked="2016")

    await _run(processor, _album("2019", "2019", "2016"))

    kwargs = _write_kwargs(writer)
    assert (kwargs["year"], kwargs["reason"]) == ("2016", "lock_restore")
    determinator.should_skip_album.assert_not_called()
    determinator.determine_album_year.assert_not_called()


@pytest.mark.asyncio
async def test_a_locked_album_at_its_year_is_left_alone() -> None:
    processor, determinator, _, _, writer = _processor(locked="2016")

    await _run(processor, _album("2016", "2016"))

    writer.assert_not_awaited()
    determinator.determine_album_year.assert_not_called()


@pytest.mark.asyncio
async def test_outside_change_skips_the_pre_checks_and_asks_the_providers() -> None:
    """Apple flipped every track: the tracks agree, so the consistent-skip would hide the change."""
    processor, determinator, _, _, writer = _processor(provider="2019")

    await _run(processor, _album("2019", "2019"), previous_years={"1": "2016", "2": "2016"})

    determinator.provider_year.assert_awaited_once()
    determinator.should_skip_album.assert_not_called()
    writer.assert_not_awaited()  # the providers agree: nothing to write


@pytest.mark.asyncio
async def test_disagreement_and_apply_writes_the_providers_year_as_an_outside_change() -> None:
    processor, _, locks, decider, writer = _processor(provider="2016", decision=DriftDecision.APPLY)

    await _run(processor, _album("2019", "2019", "2016"), previous_years={"1": "2016", "2": "2016"})

    decider.decide.assert_awaited_once_with("In Flames", "Battles", old_year="2016", new_year="2019", provider_year="2016")
    kwargs = _write_kwargs(writer)
    assert (kwargs["year"], kwargs["reason"]) == ("2016", "outside_change")
    locks.lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_keep_locks_the_new_year_and_writes_nothing() -> None:
    processor, _, locks, _, writer = _processor(provider="2016", decision=DriftDecision.KEEP)

    await _run(processor, _album("2019", "2019"), previous_years={"1": "2016", "2": "2016"})

    locks.lock.assert_awaited_once_with("In Flames", "Battles", "2019", source=LockSource.DIALOG)
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_provider_year_leaves_the_outside_change_alone() -> None:
    processor, _, locks, decider, writer = _processor(provider=None)

    await _run(processor, _album("2019"), previous_years={"1": "2016"})

    decider.decide.assert_not_awaited()
    locks.lock.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unavailable_lookup_leaves_the_outside_change_for_the_next_run() -> None:
    processor, _, _, decider, writer = _processor(provider_error=YearLookupUnavailableError("down"))

    await _run(processor, _album("2019"), previous_years={"1": "2016"})

    decider.decide.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_outside_changes_the_normal_path_writes_a_pipeline_change() -> None:
    processor, determinator, _, _, writer = _processor()
    determinator.determine_album_year = AsyncMock(return_value="2016")

    await _run(processor, _album("2019", "2016"))

    determinator.determine_album_year.assert_awaited_once()
    assert _write_kwargs(writer)["reason"] == "pipeline"


@pytest.mark.asyncio
async def test_write_album_year_returns_the_entries_it_recorded() -> None:
    processor = create_year_batch_processor()
    processor._track_updater._apply_year = AsyncMock(return_value=(["1"], 0))

    entries = await processor.write_album_year("A", "B", album_tracks=_album("2019"), year="2016", reason="lock_restore")

    assert [(e.year_before_mgu, e.year_set_by_mgu, e.reason) for e in entries] == [("2019", "2016", "lock_restore")]
