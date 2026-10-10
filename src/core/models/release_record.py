"""The release record every provider client hands to the scorer and the provider result cache stores."""

# No `from __future__ import annotations`: NotRequired must be evaluated for __required_keys__ to be right
from typing import NotRequired, TypedDict


class ReleaseRecord(TypedDict):
    """One provider release as every client hands it to the scorer, and as the provider result cache stores it.

    It holds only what the provider answered: nothing that depends on the artist context, the clock or configuration.
    """

    title: str
    year: str | None
    artist: str | None
    album_type: str | None
    country: str | None
    status: str | None
    format: str | None
    label: str | None
    source: str
    releasegroup_first_date: NotRequired[str]  # The release group's first date, for the release-group match
    genre: NotRequired[str | None]
