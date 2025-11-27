"""Artist renaming service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from src.core.models.track_status import can_edit_metadata
from src.core.models.validators import SecurityValidationError

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from src.core.tracks.track_processor import TrackProcessor
    from src.core.models.track_models import TrackDict


def _load_mapping(path: Path, error_logger: logging.Logger) -> dict[str, str]:
    """Load artist rename mapping from a YAML file."""
    if not path.exists():
        error_logger.info("Artist rename config not found at %s, skipping renames", path)
        return {}

    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        error_logger.exception("Failed to load artist rename config from %s", path)
        return {}

    if not isinstance(data, dict):
        error_logger.warning(
            "Artist rename config at %s must be a mapping of current_name -> new_name, got %s",
            path,
            type(data).__name__,
        )
        return {}

    mapping: dict[str, str] = {}
    for raw_key, raw_value in data.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            error_logger.warning(
                "Artist rename entry skipped due to non-string types: %r -> %r",
                raw_key,
                raw_value,
            )
            continue

        key = raw_key.strip()
        value = raw_value.strip()
        if not key or not value:
            error_logger.debug("Artist rename entry with empty key/value skipped: %r -> %r", raw_key, raw_value)
            continue

        mapping[key] = value

    return mapping


class ArtistRenamer:
    """Service that updates artist names based on a YAML mapping."""

    def __init__(
        self,
        track_processor: TrackProcessor,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        *,
        config_path: Path,
    ) -> None:
        self._track_processor = track_processor
        self.console_logger = console_logger
        self.error_logger = error_logger
        self._config_path = config_path
        self._mapping = _load_mapping(config_path, error_logger)

    @property
    def has_mapping(self) -> bool:
        """Return True when there is at least one rename rule."""
        return bool(self._mapping)

    def reload(self) -> None:
        """Reload mapping from the configuration file."""
        self._mapping = _load_mapping(self._config_path, self.error_logger)

    async def rename_tracks(self, tracks: list[TrackDict]) -> list[TrackDict]:
        """Rename artists according to mapping. Returns list of updated tracks."""
        if not self._mapping:
            return []

        updated_tracks: list[TrackDict] = []
        for track in tracks:
            current_artist = self._normalize_artist(track.artist)
            if current_artist is None:
                continue

            new_artist = self._mapping.get(current_artist)
            if not new_artist or new_artist == current_artist:
                continue

            if not self._can_rename_track(track, current_artist, new_artist):
                continue

            if await self._apply_rename(track, current_artist, new_artist):
                updated_tracks.append(track)

        return updated_tracks

    @staticmethod
    def _normalize_artist(artist: str | None) -> str | None:
        if not artist:
            return None
        normalized = artist.strip()
        return normalized or None

    def _can_rename_track(self, track: TrackDict, current_artist: str, new_artist: str) -> bool:
        track_status = track.track_status
        if not can_edit_metadata(track_status):
            track_id = track.id or "unknown"
            self.console_logger.debug(
                "Skipping artist rename for track %s due to read-only status '%s'",
                track_id,
                track_status or "",
            )
            return False

        if not track.id:
            self.error_logger.warning(
                "Cannot rename artist '%s' -> '%s' because track is missing ID",
                current_artist,
                new_artist,
            )
            return False

        return True

    async def _apply_rename(self, track: TrackDict, current_artist: str, new_artist: str) -> bool:
        track_id = track.id or "unknown"

        # Check if album_artist should also be updated (only if it's from the same mapping)
        # Update album_artist if it matches either the old name (key) or new name (value)
        # This handles cases where one field was already updated but the other wasn't
        # Note: album_artist is stored as extra field (TrackDict uses extra="allow")
        album_artist_raw = track.get("album_artist")
        album_artist_str = album_artist_raw if isinstance(album_artist_raw, str) else None
        album_artist = self._normalize_artist(album_artist_str)
        should_update_album_artist = album_artist is None or album_artist == current_artist

        try:
            try:
                success = await self._track_processor.update_artist_async(
                    track,
                    new_artist,
                    original_artist=current_artist,
                    update_album_artist=should_update_album_artist,
                )
            except TypeError as exc:
                if "update_album_artist" not in str(exc):
                    raise
                success = await self._track_processor.update_artist_async(
                    track,
                    new_artist,
                    original_artist=current_artist,
                )
        except (OSError, ValueError, RuntimeError, SecurityValidationError, TypeError):
            self.error_logger.exception(
                "Failed to rename artist '%s' -> '%s' for track %s",
                current_artist,
                new_artist,
                track_id,
            )
            return False

        if not success:
            return False

        # Keep local model in sync even if processor does not mutate it (e.g., tests, dry-run)
        track.artist = new_artist
        if should_update_album_artist:
            track.__dict__["album_artist"] = new_artist
        track.original_artist = current_artist

        self.console_logger.info(
            "Renamed artist '%s' -> '%s' for track %s",
            current_artist,
            new_artist,
            track_id,
        )
        return True
