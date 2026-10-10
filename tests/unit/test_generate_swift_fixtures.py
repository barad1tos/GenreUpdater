"""The Swift fixture generator runs against the app's own majority rule."""

from __future__ import annotations

from tools.generate_swift_fixtures import generate_validation_fixtures


def test_validation_fixtures_carry_the_apps_majority_rule() -> None:
    """The fixtures name the majority year and judge it with the share the app ships."""
    expected = {fixture["id"]: fixture["expected"]["majorityYear"] for fixture in generate_validation_fixtures()}

    assert expected["valid_majority_clear"] == 2020
    assert expected["valid_no_majority"] is None
    assert expected["valid_empty_years"] is None  # two of four at 0.6 is no majority; at the module's old 0.5 it was
    assert expected["valid_single_track"] == 2020
