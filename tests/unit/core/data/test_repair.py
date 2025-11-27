"""Unit tests for repair utilities."""

from pathlib import Path
from typing import Any

import pytest
from src.core.models import year_repair as repair_utils


def _make_changes_report(tmp_path: Path, rows: list[dict[str, str]]) -> dict[str, Any]:
    base = tmp_path / "logs"
    (base / "csv").mkdir(parents=True, exist_ok=True)
    report = base / "csv" / "changes_report.csv"
    # Minimal header for our repair reader
    header = [
        "change_type",
        "artist",
        "album",
        "track_name",
        "old_year",
        "new_year",
    ]
    lines = [",".join(header)]
    for r in rows:
        line = ",".join([r.get(h, "") for h in header])
        lines.append(line)
    report.write_text("\n".join(lines), encoding="utf-8")
    return {"logs_base_dir": str(base)}


def _make_backup_csv(tmp_path: Path, rows: list[dict[str, str]]) -> str:
    backup = tmp_path / "track_list_backup.csv"
    # Generic track_list header
    header = [
        "id",
        "name",
        "artist",
        "album",
        "genre",
        "date_added",
        "track_status",
        "year",
        "old_year",
        "new_year",
    ]
    lines = [",".join(header)]
    for r in rows:
        line = ",".join([r.get(h, "") for h in header])
        lines.append(line)
    backup.write_text("\n".join(lines), encoding="utf-8")
    return str(backup)


def test_build_targets_from_changes_report(tmp_path: Path) -> None:
    config = _make_changes_report(
        tmp_path,
        [
            {
                "change_type": "year_update",
                "artist": "Otep",
                "album": "Hydra",
                "track_name": "Rising",
                "old_year": "2013",
                "new_year": "2007",
            },
            {
                "change_type": "genre_update",
                "artist": "Otep",
                "album": "Hydra",
                "track_name": "Rising",
                "old_year": "",
                "new_year": "",
            },
        ],
    )

    targets = repair_utils.build_revert_targets(config=config, artist="Otep", album="Hydra")
    assert len(targets) == 1
    t = targets[0]
    assert t.track_name == "Rising"
    assert t.old_year == "2013"
    assert t.album == "Hydra"


def test_build_targets_from_backup_csv(tmp_path: Path) -> None:
    backup_path = _make_backup_csv(
        tmp_path,
        [
            {
                "id": "99816",
                "name": "Rising",
                "artist": "Otep",
                "album": "Hydra",
                "genre": "",
                "date_added": "",
                "track_status": "",
                "year": "2013",
                "old_year": "",
                "new_year": "",
            },
            {
                "id": "99822",
                "name": "Blowtorch Nightlight",
                "artist": "Otep",
                "album": "Hydra",
                "genre": "",
                "date_added": "",
                "track_status": "",
                "year": "",
                "old_year": "2013",
                "new_year": "",
            },
        ],
    )

    targets = repair_utils.build_revert_targets(config={}, artist="Otep", album="Hydra", backup_csv_path=backup_path)
    # Both rows should be used since one has year, another has old_year
    assert len(targets) == 2
    years = sorted(t.old_year for t in targets)
    assert years == ["2013", "2013"]


@pytest.mark.asyncio
async def test_apply_year_reverts_matches_by_id_and_album_name() -> None:
    """Test that year reverts correctly match tracks by ID and album/name combination."""

    class MockTrackProcessor:
        """Mock track processor for testing."""

        def __init__(self) -> None:
            self.updated: list[tuple[str, str]] = []

        @staticmethod
        async def fetch_tracks_async(artist: str, **_kwargs: Any) -> list[dict[str, str]]:
            """Fetch mock tracks for testing."""
            # Return two tracks: one with matching id, one matched by (album, name)
            return [
                {"id": "1", "name": "Rising", "artist": artist, "album": "Hydra", "track_status": "subscription", "year": "2007"},
                {"id": "2", "name": "Zero", "artist": artist, "album": "Generation Doom", "track_status": "subscription", "year": "2007"},
            ]

        async def update_track_async(
            self,
            *,
            track_id: str,
            new_year: str | None = None,
            **_kwargs: Any,
        ) -> bool:
            """Update track year in mock storage."""
            if new_year:
                self.updated.append((track_id, new_year))
            return True

    tp = MockTrackProcessor()

    # Two targets: one by ID, one by (album, name)
    targets = [
        repair_utils.RevertTarget(track_id="1", track_name="Rising", album="Hydra", old_year="2013"),
        repair_utils.RevertTarget(track_id=None, track_name="Zero", album="Generation Doom", old_year="2016"),
    ]

    updated, missing, change_log = await repair_utils.apply_year_reverts(
        track_processor=tp,
        artist="Otep",
        targets=targets,
    )

    assert updated == 2
    assert missing == 0
    assert sorted(tp.updated) == [("1", "2013"), ("2", "2016")]
    assert len(change_log) == 2
