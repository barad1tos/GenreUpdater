"""Prerelease track detection and handling.

Determines how to handle albums containing prerelease tracks
based on the configured handling mode (skip_all, mark_only, process_editable).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.models.track_status import can_edit_metadata, is_prerelease_status

if TYPE_CHECKING:
    import logging

    from core.models.protocols import PendingVerificationServiceProtocol
    from core.models.track_models import AppConfig
    from core.models.types import TrackDict


class PrereleaseHandler:
    """Handles prerelease track detection and filtering.

    Responsibilities:
    - Detect prerelease tracks in an album
    - Apply configured handling mode (skip_all, mark_only, process_editable)
    - Mark albums for verification when appropriate
    """

    def __init__(
        self,
        *,
        console_logger: logging.Logger,
        config: AppConfig,
        pending_verification: PendingVerificationServiceProtocol,
        prerelease_recheck_days: int,
    ) -> None:
        self.console_logger = console_logger
        self.config = config
        self.pending_verification = pending_verification
        self.prerelease_recheck_days = prerelease_recheck_days

    async def handle_prerelease_tracks(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
    ) -> tuple[bool, list[TrackDict]]:
        """Handle prerelease track detection and filtering.

        Checks for prerelease tracks and applies the configured handling mode.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of all tracks in the album

        Returns:
            Tuple of (should_continue, editable_tracks):
            - should_continue: False if processing should stop
            - editable_tracks: Filtered list of tracks that can be edited

        """
        original_track_count = len(album_tracks)
        prerelease_tracks = [track for track in album_tracks if is_prerelease_status(track.track_status)]
        has_prerelease = len(prerelease_tracks) > 0
        editable_tracks = [track for track in album_tracks if can_edit_metadata(track.track_status)]

        if not has_prerelease:
            return True, editable_tracks

        prerelease_handling = self._get_prerelease_handling_mode(artist, album)

        if prerelease_handling == "skip_all":
            self.console_logger.info(
                "[SKIP] %s - %s: contains prerelease tracks (%d/%d) - skip_all mode",
                artist,
                album,
                len(prerelease_tracks),
                original_track_count,
            )
            return False, []

        if prerelease_handling == "mark_only":
            self.console_logger.info(
                "[MARK] %s - %s: %d prerelease + %d editable - mark_only mode",
                artist,
                album,
                len(prerelease_tracks),
                len(editable_tracks),
            )
            await self.pending_verification.mark_for_verification(
                artist,
                album,
                reason="prerelease",
                metadata={
                    "track_count": str(original_track_count),
                    "prerelease_count": str(len(prerelease_tracks)),
                    "editable_count": str(len(editable_tracks)),
                    "mode": "mark_only",
                },
                recheck_days=self.prerelease_recheck_days,
            )
            return False, []

        # process_editable mode: check if we have any editable tracks
        if not editable_tracks:
            self.console_logger.debug(
                "Skipping album '%s - %s': no editable tracks (%d/%d tracks are prerelease)",
                artist,
                album,
                len(prerelease_tracks),
                original_track_count,
            )
            await self.pending_verification.mark_for_verification(
                artist,
                album,
                reason="prerelease",
                metadata={
                    "track_count": str(original_track_count),
                    "prerelease_count": str(len(prerelease_tracks)),
                    "all_prerelease": "true",
                },
                recheck_days=self.prerelease_recheck_days,
            )
            return False, []

        # Mixed album: mark for verification but continue processing editable tracks
        self.console_logger.info(
            "[MIXED] %s - %s: %d prerelease + %d editable - marking for verification, processing editable",
            artist,
            album,
            len(prerelease_tracks),
            len(editable_tracks),
        )
        await self.pending_verification.mark_for_verification(
            artist,
            album,
            reason="prerelease",
            metadata={
                "track_count": str(original_track_count),
                "prerelease_count": str(len(prerelease_tracks)),
                "editable_count": str(len(editable_tracks)),
                "mixed_album": "true",
            },
            recheck_days=self.prerelease_recheck_days,
        )
        return True, editable_tracks

    def _get_prerelease_handling_mode(self, artist: str, album: str) -> str:
        """Get validated prerelease handling mode from config.

        Args:
            artist: Artist name (for warning context)
            album: Album name (for warning context)

        Returns:
            Valid prerelease handling mode: 'process_editable', 'skip_all', or 'mark_only'

        """
        valid_modes = {"process_editable", "skip_all", "mark_only"}
        mode = self.config.year_retrieval.processing.prerelease_handling

        if mode not in valid_modes:
            self.console_logger.warning(
                "Unknown prerelease_handling mode '%s' for %s - %s, defaulting to 'process_editable'. Valid options: %s",
                mode,
                artist,
                album,
                ", ".join(sorted(valid_modes)),
            )
            return "process_editable"

        return mode
