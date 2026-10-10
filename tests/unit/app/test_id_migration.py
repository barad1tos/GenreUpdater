"""Tests for the one-time move of the saved track list to Music.app persistent IDs."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.id_migration import (
    BACKUP_NAME,
    TrackListMigrationError,
    carry_year_history,
    is_persistent_id,
    migrate_to_persistent_ids,
    needs_migration,
)
from core.logger import get_full_log_path
from core.models.track_models import AppConfig, TrackDict
from metrics.track_sync import load_track_list, save_track_map_to_csv, sync_track_list_with_current
from tests.factories import create_test_app_config

_LOGGER = logging.getLogger("test.id_migration")
_PERSISTENT_ID = "6342D31846D0E960"


def _old_row(track_id: str, *, before: str = "1999", set_by_mgu: str = "2001") -> TrackDict:
    """A track list row keyed by an old Music.app id."""
    return TrackDict(id=track_id, name="Song", artist="A", album="B", year_before_mgu=before, year_set_by_mgu=set_by_mgu)


def _fresh_track() -> TrackDict:
    """The same track as Music.app reports it now, keyed by its persistent ID."""
    return TrackDict(id=_PERSISTENT_ID, name="Song", artist="A", album="B")


class TestIdFormat:
    """Only the 16-hex persistent ID format counts as migrated."""

    def test_persistent_ids_are_recognised(self) -> None:
        assert is_persistent_id(_PERSISTENT_ID)
        assert is_persistent_id("0000123456789012")  # some persistent IDs are all digits

    def test_other_ids_are_not(self) -> None:
        assert not is_persistent_id("111701")
        assert not is_persistent_id(_PERSISTENT_ID.lower())

    def test_decimal_ids_need_migration(self) -> None:
        assert needs_migration({"111701": _old_row("111701")})

    def test_an_empty_list_needs_none(self) -> None:
        assert not needs_migration({})

    def test_persistent_ids_are_not_migrated_again(self) -> None:
        assert not needs_migration({"0000123456789012": _old_row("0000123456789012")})


class TestCarryYearHistory:
    """Year history moves to the track a row describes, matched by artist, album and name."""

    def test_unique_match_carries_history(self) -> None:
        tracks = [_fresh_track()]

        assert carry_year_history([_old_row("1")], tracks) == 1
        assert (tracks[0].year_before_mgu, tracks[0].year_set_by_mgu) == ("1999", "2001")

    def test_equal_duplicates_carry_history(self) -> None:
        tracks = [_fresh_track()]

        assert carry_year_history([_old_row("1"), _old_row("2")], tracks) == 1

    def test_conflicting_duplicates_get_no_history(self) -> None:
        tracks = [_fresh_track()]

        assert carry_year_history([_old_row("1"), _old_row("2", before="1990", set_by_mgu="1991")], tracks) == 0
        assert (tracks[0].year_before_mgu, tracks[0].year_set_by_mgu) == (None, None)

    def test_a_duplicate_without_history_makes_the_match_ambiguous(self) -> None:
        tracks = [_fresh_track()]

        assert carry_year_history([_old_row("1"), _old_row("2", before="", set_by_mgu="")], tracks) == 0

    def test_a_year_before_alone_is_carried(self) -> None:
        tracks = [_fresh_track()]

        assert carry_year_history([_old_row("1", set_by_mgu="")], tracks) == 1
        assert (tracks[0].year_before_mgu, tracks[0].year_set_by_mgu) == ("1999", None)

    def test_unmatched_track_gets_no_history(self) -> None:
        tracks = [TrackDict(id=_PERSISTENT_ID, name="Other", artist="A", album="B")]

        assert carry_year_history([_old_row("1")], tracks) == 0


def _processor(tracks: list[TrackDict], library_ids: list[str] | None = None, rejected_ids: frozenset[str] = frozenset()) -> MagicMock:
    """A track processor whose library holds `library_ids` (by default the ids of `tracks`) and whose fetch returns `tracks`."""
    processor = MagicMock()
    processor.rejected_track_ids = rejected_ids
    processor.fetch_tracks_in_batches = AsyncMock(return_value=tracks)
    processor.ap_client.fetch_all_track_ids = AsyncMock(return_value=library_ids if library_ids is not None else [str(t.id) for t in tracks])
    return processor


class TestMigrate:
    """The migration rewrites the track list once, and changes nothing unless the library fetch worked."""

    @staticmethod
    def _write_old_list(tmp_path: Path) -> tuple[AppConfig, Path]:
        config = create_test_app_config(logs_base_dir=str(tmp_path))
        csv_path = Path(get_full_log_path(config, "csv_output_file", "csv/track_list.csv"))
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        save_track_map_to_csv({"111701": _old_row("111701")}, str(csv_path), _LOGGER, _LOGGER)
        return config, csv_path

    @pytest.mark.asyncio
    async def test_migration_backs_up_and_rewrites_the_track_list(self, tmp_path: Path) -> None:
        config, csv_path = self._write_old_list(tmp_path)
        processor = _processor([_fresh_track()])
        snapshot_service = MagicMock()

        migrated = await migrate_to_persistent_ids(
            config=config, track_processor=processor, snapshot_service=snapshot_service, console_logger=_LOGGER, error_logger=_LOGGER
        )

        assert migrated
        assert (csv_path.parent / BACKUP_NAME).exists()
        rows = load_track_list(str(csv_path))
        assert list(rows) == [_PERSISTENT_ID]
        assert (rows[_PERSISTENT_ID].year_before_mgu, rows[_PERSISTENT_ID].year_set_by_mgu) == ("1999", "2001")
        snapshot_service.clear_delta.assert_called_once()

    @pytest.mark.asyncio
    async def test_carried_history_survives_the_next_sync(self, tmp_path: Path) -> None:
        """The run after the migration syncs the list; the carried year before the tool must stay for revert."""
        config, csv_path = self._write_old_list(tmp_path)
        await migrate_to_persistent_ids(
            config=config, track_processor=_processor([_fresh_track()]), snapshot_service=MagicMock(), console_logger=_LOGGER, error_logger=_LOGGER
        )
        current = TrackDict(id=_PERSISTENT_ID, name="Song", artist="A", album="B", year="2001")

        await sync_track_list_with_current([current], str(csv_path), console_logger=_LOGGER, error_logger=_LOGGER)

        assert load_track_list(str(csv_path))[_PERSISTENT_ID].year_before_mgu == "1999"

    @pytest.mark.asyncio
    async def test_failed_write_stops_the_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A track list that could not be written must not count as migrated, or the next sync drops its history."""
        config, csv_path = self._write_old_list(tmp_path)
        snapshot_service = MagicMock()

        def refuse(_self: Path, _target: Path) -> Path:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(Path, "replace", refuse)
        with pytest.raises(TrackListMigrationError):
            await migrate_to_persistent_ids(
                config=config,
                track_processor=_processor([_fresh_track()]),
                snapshot_service=snapshot_service,
                console_logger=_LOGGER,
                error_logger=_LOGGER,
            )

        monkeypatch.undo()
        assert list(load_track_list(str(csv_path))) == ["111701"]
        snapshot_service.clear_delta.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_run_is_retried_from_the_original_list(self, tmp_path: Path) -> None:
        config, csv_path = self._write_old_list(tmp_path)
        original = csv_path.read_bytes()
        with pytest.raises(TrackListMigrationError):
            await migrate_to_persistent_ids(
                config=config,
                track_processor=_processor([_fresh_track()], [_PERSISTENT_ID, "0A1B2C3D4E5F6071"]),
                snapshot_service=MagicMock(),
                console_logger=_LOGGER,
                error_logger=_LOGGER,
            )

        assert await migrate_to_persistent_ids(
            config=config, track_processor=_processor([_fresh_track()]), snapshot_service=MagicMock(), console_logger=_LOGGER, error_logger=_LOGGER
        )
        assert (csv_path.parent / BACKUP_NAME).read_bytes() == original
        assert load_track_list(str(csv_path))[_PERSISTENT_ID].year_before_mgu == "1999"

    @pytest.mark.asyncio
    async def test_unread_tracks_are_named_in_the_log(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A track that never reads back blocks every run, so the log must say which one it is."""
        config, _ = self._write_old_list(tmp_path)

        with caplog.at_level(logging.ERROR, logger=_LOGGER.name), pytest.raises(TrackListMigrationError):
            await migrate_to_persistent_ids(
                config=config,
                track_processor=_processor([_fresh_track()], [_PERSISTENT_ID, "0A1B2C3D4E5F6071"]),
                snapshot_service=MagicMock(),
                console_logger=_LOGGER,
                error_logger=_LOGGER,
            )

        assert "0A1B2C3D4E5F6071" in caplog.text

    @pytest.mark.parametrize(
        ("fetched", "library_ids"),
        [
            ([], None),  # Music.app did not answer
            ([TrackDict(id="111701", name="Song", artist="A", album="B")], None),  # scripts still emit old ids
            ([_fresh_track()], [_PERSISTENT_ID, "0A1B2C3D4E5F6071"]),  # a batch failed, so the fetch is short
        ],
        ids=["empty", "old-ids", "incomplete"],
    )
    @pytest.mark.asyncio
    async def test_unusable_fetch_stops_the_run_and_changes_nothing(
        self, tmp_path: Path, fetched: list[TrackDict], library_ids: list[str] | None
    ) -> None:
        """Without the whole library keyed by persistent ID, nothing is written and the run stops before any reader."""
        config, csv_path = self._write_old_list(tmp_path)
        snapshot_service = MagicMock()

        with pytest.raises(TrackListMigrationError):
            await migrate_to_persistent_ids(
                config=config,
                track_processor=_processor(fetched, library_ids),
                snapshot_service=snapshot_service,
                console_logger=_LOGGER,
                error_logger=_LOGGER,
            )

        assert not (csv_path.parent / BACKUP_NAME).exists()
        assert list(load_track_list(str(csv_path))) == ["111701"]
        snapshot_service.clear_delta.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_track_validation_rejects_does_not_block_the_migration(self, tmp_path: Path) -> None:
        """A rejected track reads the same way on every run, so waiting for it would block the tool for good."""
        config, csv_path = self._write_old_list(tmp_path)

        migrated = await migrate_to_persistent_ids(
            config=config,
            track_processor=_processor([_fresh_track()], [_PERSISTENT_ID, "0A1B2C3D4E5F6071"], frozenset({"0A1B2C3D4E5F6071"})),
            snapshot_service=MagicMock(),
            console_logger=_LOGGER,
            error_logger=_LOGGER,
        )

        assert migrated
        assert list(load_track_list(str(csv_path))) == [_PERSISTENT_ID]

    @pytest.mark.asyncio
    async def test_existing_backup_is_kept(self, tmp_path: Path) -> None:
        """A second migration must not replace the original list's backup."""
        config, csv_path = self._write_old_list(tmp_path)
        backup = csv_path.parent / BACKUP_NAME
        backup.write_text("original", encoding="utf-8")

        migrated = await migrate_to_persistent_ids(
            config=config, track_processor=_processor([_fresh_track()]), snapshot_service=MagicMock(), console_logger=_LOGGER, error_logger=_LOGGER
        )

        assert migrated
        assert list(load_track_list(str(csv_path))) == [_PERSISTENT_ID]
        assert backup.read_text(encoding="utf-8") == "original"

    @pytest.mark.asyncio
    async def test_migrated_list_is_left_alone(self, tmp_path: Path) -> None:
        config = create_test_app_config(logs_base_dir=str(tmp_path))
        csv_path = Path(get_full_log_path(config, "csv_output_file", "csv/track_list.csv"))
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        save_track_map_to_csv({_PERSISTENT_ID: _fresh_track()}, str(csv_path), _LOGGER, _LOGGER)
        processor = MagicMock()
        processor.fetch_tracks_in_batches = AsyncMock()

        migrated = await migrate_to_persistent_ids(
            config=config, track_processor=processor, snapshot_service=MagicMock(), console_logger=_LOGGER, error_logger=_LOGGER
        )

        assert not migrated
        processor.fetch_tracks_in_batches.assert_not_awaited()
