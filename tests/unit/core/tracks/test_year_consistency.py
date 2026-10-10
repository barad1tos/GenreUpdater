"""Tests for the majority year an album's own tracks can supply."""

from __future__ import annotations

import logging

import pytest

from core.models.track_models import TrackDict
from core.tracks.year_consistency import YearConsistencyChecker


def _tracks(*years: str) -> list[TrackDict]:
    return [TrackDict(id=f"{index:016X}", name=f"Track {index}", artist="A", album="B", year=year or None) for index, year in enumerate(years)]


@pytest.fixture
def checker() -> YearConsistencyChecker:
    """A checker with the default majority share."""
    return YearConsistencyChecker(console_logger=logging.getLogger("test.year_consistency"))


class TestMajorityYear:
    """The majority is a last resort for albums no provider knows, so it must be a real majority."""

    def test_most_of_the_tracks_decide(self, checker: YearConsistencyChecker) -> None:
        assert checker.get_majority_year(_tracks(*["2016"] * 13, "2026")) == "2016"

    def test_a_split_has_no_majority(self, checker: YearConsistencyChecker) -> None:
        assert checker.get_majority_year(_tracks("2016", "2016", "2019", "2019")) is None

    def test_unset_years_count_against_the_majority(self, checker: YearConsistencyChecker) -> None:
        assert checker.get_majority_year(_tracks("2016", "2016", "", "", "0")) is None

    def test_no_years_no_majority(self, checker: YearConsistencyChecker) -> None:
        assert checker.get_majority_year(_tracks("", "0")) is None

    def test_the_share_is_configurable(self) -> None:
        strict = YearConsistencyChecker(console_logger=logging.getLogger("test"), dominance_min_share=0.9)

        assert strict.get_majority_year(_tracks(*["2016"] * 8, "2017", "2018")) is None


class TestGetEarliestTrackAddedYear:
    """The earliest year a track was added, handed to the providers as a hint."""

    @staticmethod
    def _track(index: int, date_added: str | None) -> TrackDict:
        return TrackDict(id=str(index), name=f"T{index}", artist="A", album="B", genre="G", year="2020", date_added=date_added)

    def test_returns_earliest_year_from_tracks(self) -> None:
        tracks = [self._track(1, "2023-05-15"), self._track(2, "2021-03-10"), self._track(3, "2024-01-01")]

        assert YearConsistencyChecker.get_earliest_track_added_year(tracks) == 2021

    def test_returns_none_for_empty_tracks(self) -> None:
        assert YearConsistencyChecker.get_earliest_track_added_year([]) is None

    def test_returns_none_when_no_date_added(self) -> None:
        assert YearConsistencyChecker.get_earliest_track_added_year([self._track(1, ""), self._track(2, None)]) is None

    def test_skips_unparseable_dates(self) -> None:
        tracks = [self._track(1, "invalid"), self._track(2, "2022-06-01"), self._track(3, "")]

        assert YearConsistencyChecker.get_earliest_track_added_year(tracks) == 2022

    def test_reads_the_year_of_a_datetime(self) -> None:
        assert YearConsistencyChecker.get_earliest_track_added_year([self._track(1, "2025-10-01 00:19:04")]) == 2025
