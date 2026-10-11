"""Which tracks changed year outside the tool, and what the change looks like for an album."""

from __future__ import annotations

from core.models.track_models import TrackDict
from core.tracks.year_drift import OutsideYearChange, find_outside_changes, summarize_outside_changes


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
