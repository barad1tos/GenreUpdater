"""Tests for LibrarySnapshotService."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest

from src.services.cache.snapshot import (
    DELTA_MAX_TRACKED_IDS,
    LibraryCacheMetadata,
    LibraryDeltaCache,
    LibrarySnapshotService,
)
from src.core.models.track_models import TrackDict


def _make_config(tmp_path: pytest.TempPathFactory, *, compress: bool = False) -> dict:
    root = tmp_path.mktemp("cache-root")
    music_library = root / "Music Library.musiclibrary"
    music_library.write_text("", encoding="utf-8")
    return {
        "logs_base_dir": str(root),
        "music_library_path": str(music_library),
        "caching": {
            "library_snapshot": {
                "enabled": True,
                "delta_enabled": True,
                "cache_file": "cache/library_snapshot.json",
                "compress": compress,
                "compress_level": 6,
            }
        },
    }


def _make_tracks() -> list[TrackDict]:
    return [
        TrackDict(id="1", name="Alpha", artist="Artist A", album="Album A"),
        TrackDict(id="2", name="Beta", artist="Artist B", album="Album B"),
    ]


@pytest.mark.asyncio
async def test_save_and_load_snapshot(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = _make_config(tmp_path_factory)
    service = LibrarySnapshotService(config, logging.getLogger("test.snapshot"))
    await service.initialize()

    tracks = _make_tracks()
    await service.save_snapshot(tracks)

    loaded = await service.load_snapshot()
    assert loaded is not None
    assert len(loaded) == len(tracks)
    assert loaded[0].id == "1"


@pytest.mark.asyncio
async def test_snapshot_with_gzip_compression(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = _make_config(tmp_path_factory, compress=True)
    service = LibrarySnapshotService(config, logging.getLogger("test.snapshot.gzip"))
    await service.initialize()

    tracks = _make_tracks()
    await service.save_snapshot(tracks)

    snapshot_path = service._snapshot_path
    assert snapshot_path.suffix == ".gz"
    assert snapshot_path.exists()

    loaded = await service.load_snapshot()
    assert loaded is not None
    assert [track.id for track in loaded] == ["1", "2"]


@pytest.mark.asyncio
async def test_corrupted_snapshot_returns_none(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = _make_config(tmp_path_factory)
    service = LibrarySnapshotService(config, logging.getLogger("test.snapshot.corrupt"))
    await service.initialize()

    tracks = _make_tracks()
    await service.save_snapshot(tracks)

    snapshot_path = service._snapshot_path
    snapshot_path.write_bytes(b"not-json")

    loaded = await service.load_snapshot()
    assert loaded is None


@pytest.mark.asyncio
async def test_delta_cache_persistence(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = _make_config(tmp_path_factory)
    service = LibrarySnapshotService(config, logging.getLogger("test.snapshot.delta"))
    await service.initialize()

    delta = LibraryDeltaCache(
        last_run=datetime.now(),
        processed_track_ids={"10", "20"},
        field_hashes={"genres": "abc"},
        tracked_since=datetime.now(),
    )
    await service.save_delta(delta)

    loaded_delta = await service.load_delta()
    assert loaded_delta is not None
    assert loaded_delta.processed_track_ids == {"10", "20"}
    assert loaded_delta.field_hashes["genres"] == "abc"


def test_delta_exceeds_limit_resets() -> None:
    delta = LibraryDeltaCache(last_run=datetime.now(), processed_track_ids=set(), field_hashes={})
    delta.processed_track_ids = {str(i) for i in range(DELTA_MAX_TRACKED_IDS)}
    delta.add_processed_ids(["extra"])
    assert delta.processed_track_ids == {"extra"}


def test_metadata_serialization_roundtrip() -> None:
    now = datetime.now()
    metadata = LibraryCacheMetadata(
        last_full_scan=now,
        library_mtime=now - timedelta(minutes=1),
        track_count=42,
        snapshot_hash="deadbeef",
    )
    assert LibraryCacheMetadata.from_dict(metadata.to_dict()) == metadata
