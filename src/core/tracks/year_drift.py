"""Years that changed outside the tool: finding them and deciding what happens to them."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

from core.apple_script_names import ASK_YEAR_DRIFT
from core.logger import PLAIN_TEXT
from core.presence import is_user_present
from core.tracks.track_delta import year_text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from core.models.protocols import AppleScriptClientProtocol
    from core.models.track_models import AppConfig, TrackDict

    # True: someone is there; False: nobody; None: the idle time could not be read
    PresenceCheck = Callable[[int], Awaitable[bool | None]]

DIALOG_GRACE_SECONDS = 15
NO_YEAR_LABEL = "—"


class OutsideYearChange(NamedTuple):
    """A track whose year changed since the last snapshot, other than by the tool's own write."""

    track: TrackDict
    old_year: str
    new_year: str


def find_outside_changes(album_tracks: Sequence[TrackDict], previous_years: Mapping[str, str] | None) -> list[OutsideYearChange]:
    """Return the album's tracks whose year differs from the one the last snapshot held, except the tool's own writes.

    Args:
        album_tracks: The album's tracks as read from Music.app
        previous_years: Track id → the year the last snapshot held, for tracks whose year differs; None or empty when there is no baseline

    Returns:
        One entry per changed track, in album order
    """
    if not previous_years:
        return []
    changes: list[OutsideYearChange] = []
    for track in album_tracks:
        previous = previous_years.get(str(track.id))
        if previous is None:
            continue
        old_year, new_year = year_text(previous), year_text(track.year)
        if old_year == new_year or (track.year_set_by_mgu and new_year == year_text(track.year_set_by_mgu)):
            continue
        changes.append(OutsideYearChange(track, old_year, new_year))
    return changes


def summarize_outside_changes(changes: Sequence[OutsideYearChange]) -> tuple[str, str]:
    """Return the most common old year and the most common new year among the changes (a tie goes to the first seen).

    Args:
        changes: The album's outside changes, at least one

    Returns:
        The old and the new year

    Raises:
        ValueError: No changes were given
    """
    if not changes:
        msg = "A summary needs at least one change"
        raise ValueError(msg)
    old_year = Counter(change.old_year for change in changes).most_common(1)[0][0]
    new_year = Counter(change.new_year for change in changes).most_common(1)[0][0]
    return old_year, new_year


class DriftDecision(StrEnum):
    """What happens to a year that changed outside the tool when the providers disagree with it."""

    KEEP = "keep"
    APPLY = "apply"


class YearDriftDecider:
    """Asks the person at the Mac about a disputed outside change, or applies the providers' year when nobody is there.

    One dialog per album, at most `drift_dialog_max_per_run` per run (a person edits a few albums; a batch is Apple),
    giving up after `drift_dialog_timeout_seconds`. A dry run never asks and never writes, so it decides APPLY and logs.

    Args:
        ap_client: Runs `ask_year_drift.applescript` with the dialog's values as arguments
        config: Typed application configuration (`year_retrieval.processing`)
        console_logger: Logger for the decisions
        dry_run: True when nothing is written and nobody is asked
        is_present: Presence check (injectable for tests)
    """

    def __init__(
        self,
        *,
        ap_client: AppleScriptClientProtocol,
        config: AppConfig,
        console_logger: logging.Logger,
        dry_run: bool,
        is_present: PresenceCheck = is_user_present,
    ) -> None:
        processing = config.year_retrieval.processing
        self._ap_client = ap_client
        self._console_logger = console_logger
        self._dry_run = dry_run
        self._is_present = is_present
        self._idle_threshold = processing.presence_idle_seconds
        self._timeout = processing.drift_dialog_timeout_seconds
        self._max_dialogs = processing.drift_dialog_max_per_run
        self._dialogs_shown = 0
        self._presence_unknown_said = False
        # Albums are judged concurrently; the lock serialises the cap check, the presence check and the dialog,
        # so two albums cannot both take the last slot
        self._dialog_lock = asyncio.Lock()

    async def decide(self, artist: str, album: str, *, old_year: str, new_year: str, provider_year: str) -> DriftDecision:
        """Decide whether the album keeps `new_year` (and gets locked) or takes `provider_year`.

        Args:
            artist: Album artist
            album: Album name
            old_year: The year the last snapshot held ("" when none)
            new_year: The year the library holds now
            provider_year: The providers' year, which disagrees with `new_year`

        Returns:
            KEEP when the person chose the new year, APPLY otherwise
        """
        if self._dry_run:
            self._console_logger.info(
                "DRY-RUN: would ask about '%s - %s' (%s -> %s, providers %s); applying",
                artist,
                album,
                old_year or NO_YEAR_LABEL,
                new_year,
                provider_year,
                extra=PLAIN_TEXT,
            )
            return DriftDecision.APPLY
        async with self._dialog_lock:
            if self._dialogs_shown >= self._max_dialogs:
                self._console_logger.info(
                    "Dialog cap (%d) reached this run; applying %s to '%s - %s'", self._max_dialogs, provider_year, artist, album, extra=PLAIN_TEXT
                )
                return DriftDecision.APPLY
            present = await self._is_present(self._idle_threshold)
            if present is None:
                self._say_presence_unknown(artist, album, provider_year)
                return DriftDecision.APPLY
            if not present:
                self._console_logger.info("Nobody at the Mac; applying %s to '%s - %s'", provider_year, artist, album, extra=PLAIN_TEXT)
                return DriftDecision.APPLY
            self._dialogs_shown += 1
            answer = await self._ap_client.run_script(
                ASK_YEAR_DRIFT,
                [artist, album, old_year or NO_YEAR_LABEL, new_year, provider_year, str(self._timeout)],
                timeout=self._timeout + DIALOG_GRACE_SECONDS,
                context_artist=artist,
                context_album=album,
            )
        verdict = (answer or "").strip()
        if verdict == DriftDecision.KEEP.value:
            self._console_logger.info("Kept %s on '%s - %s' at the person's request; locking it", new_year, artist, album, extra=PLAIN_TEXT)
            return DriftDecision.KEEP
        self._console_logger.info(
            "Dialog for '%s - %s' answered '%s'; applying %s", artist, album, verdict or "nothing", provider_year, extra=PLAIN_TEXT
        )
        return DriftDecision.APPLY

    def _say_presence_unknown(self, artist: str, album: str, provider_year: str) -> None:
        """Say that the idle time could not be read: a warning the first time, so a broken `ioreg` is noticed once per run."""
        level = logging.INFO if self._presence_unknown_said else logging.WARNING
        self._presence_unknown_said = True
        self._console_logger.log(
            level, "Presence unknown (the idle time could not be read); applying %s to '%s - %s'", provider_year, artist, album, extra=PLAIN_TEXT
        )
