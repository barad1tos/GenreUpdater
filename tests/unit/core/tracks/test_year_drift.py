"""Which tracks changed year outside the tool, and what the change looks like for an album."""

from __future__ import annotations

import logging
from typing import cast

import pytest

from core.apple_script_names import ASK_YEAR_DRIFT
from core.models.protocols import AppleScriptClientProtocol
from core.models.track_models import TrackDict
from core.tracks.year_drift import DriftDecision, OutsideYearChange, YearDriftDecider, find_outside_changes, summarize_outside_changes
from tests.factories import create_test_app_config
from tests.mocks.protocol_mocks import MockAppleScriptClient


def _track(track_id: str, year: str | None, *, set_by_mgu: str | None = None) -> TrackDict:
    return TrackDict(id=track_id, name=f"T{track_id}", artist="In Flames", album="Battles", year=year, year_set_by_mgu=set_by_mgu)


def test_a_track_whose_year_differs_from_the_snapshot_is_an_outside_change() -> None:
    changes = find_outside_changes([_track("1", "2019"), _track("2", "2016")], {"1": "2016"})

    assert changes == [OutsideYearChange(_track("1", "2019"), "2016", "2019")]


def test_the_tools_own_write_is_not_an_outside_change() -> None:
    """A run that crashed before persisting the snapshot leaves the tool's writes looking new; the record tells them apart."""
    assert find_outside_changes([_track("1", "2016", set_by_mgu="2016")], {"1": "2019"}) == []


def test_no_baseline_means_no_changes() -> None:
    assert find_outside_changes([_track("1", "2019")], None) == []
    assert find_outside_changes([_track("1", "2019")], {}) == []


def test_an_unset_year_counts_as_empty_on_both_sides() -> None:
    assert find_outside_changes([_track("1", None), _track("2", "0")], {"1": "0", "2": ""}) == []


def test_summary_is_the_most_common_old_and_new_year() -> None:
    changes = [OutsideYearChange(_track(str(i), "2019"), "2016", "2019") for i in range(13)] + [
        OutsideYearChange(_track("x", "2020"), "2015", "2020")
    ]

    assert summarize_outside_changes(changes) == ("2016", "2019")


# --- the decider ---


def _decider(
    *, present: bool = True, dry_run: bool = False, answer: str | None = "fix", max_per_run: int = 3
) -> tuple[YearDriftDecider, MockAppleScriptClient]:
    client = MockAppleScriptClient()
    client.set_response(ASK_YEAR_DRIFT, answer)
    config = create_test_app_config()
    config.year_retrieval.processing.drift_dialog_max_per_run = max_per_run

    async def is_present(_threshold: int) -> bool:
        return present

    ap_client = cast(AppleScriptClientProtocol, cast(object, client))
    return YearDriftDecider(
        ap_client=ap_client, config=config, console_logger=logging.getLogger("test"), dry_run=dry_run, is_present=is_present
    ), client


@pytest.mark.asyncio
async def test_keep_locks_the_new_year() -> None:
    decider, _ = _decider(answer="keep")

    assert await decider.decide("In Flames", "Battles", old_year="2016", new_year="2019", provider_year="2016") is DriftDecision.KEEP


@pytest.mark.asyncio
async def test_fix_timeout_and_silence_apply_the_providers_year() -> None:
    for answer in ("fix", "timeout", None):
        decider, _ = _decider(answer=answer)
        assert await decider.decide("A", "B", old_year="2016", new_year="2019", provider_year="2016") is DriftDecision.APPLY


@pytest.mark.asyncio
async def test_nobody_at_the_mac_means_apply_without_a_dialog() -> None:
    decider, client = _decider(present=False, answer="keep")

    assert await decider.decide("A", "B", old_year="2016", new_year="2019", provider_year="2016") is DriftDecision.APPLY
    assert client.scripts_run == []


@pytest.mark.asyncio
async def test_a_dry_run_never_asks() -> None:
    decider, client = _decider(dry_run=True, answer="keep")

    assert await decider.decide("A", "B", old_year="2016", new_year="2019", provider_year="2016") is DriftDecision.APPLY
    assert client.scripts_run == []


@pytest.mark.asyncio
async def test_the_dialog_cap_holds_per_run() -> None:
    decider, client = _decider(answer="keep", max_per_run=1)

    first = await decider.decide("A", "B", old_year="2016", new_year="2019", provider_year="2016")
    second = await decider.decide("C", "D", old_year="2016", new_year="2019", provider_year="2016")

    assert (first, second) == (DriftDecision.KEEP, DriftDecision.APPLY)
    assert len(client.scripts_run) == 1


@pytest.mark.asyncio
async def test_dialog_arguments_travel_verbatim() -> None:
    decider, client = _decider(answer="fix")

    await decider.decide('Royal "Crown" Revue', "Mugzy's Move \\ Паліндром", old_year="", new_year="1991", provider_year="1996")

    script, arguments = client.scripts_run[0]
    assert script == ASK_YEAR_DRIFT
    assert arguments == ['Royal "Crown" Revue', "Mugzy's Move \\ Паліндром", "—", "1991", "1996", "120"]
