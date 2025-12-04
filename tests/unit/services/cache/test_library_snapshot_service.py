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


@pytest.mark.asyncio
async def test_concurrent_writes_are_serialized(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Test that concurrent snapshot writes are serialized via lock."""
    import asyncio

    config = _make_config(tmp_path_factory)
    service = LibrarySnapshotService(config, logging.getLogger("test.snapshot"))
    await service.initialize()

    # Track execution order
    execution_order: list[str] = []

    original_write = service._write_bytes_atomic

    def tracking_write(path, data):
        execution_order.append(f"start_{len(data)}")
        original_write(path, data)
        execution_order.append(f"end_{len(data)}")

    service._write_bytes_atomic = tracking_write  # type: ignore[method-assign]

    tracks1 = [TrackDict(id="1", name="Track1", artist="Artist1", album="Album1")]
    tracks2 = [
        TrackDict(id="2", name="Track2", artist="Artist2", album="Album2"),
        TrackDict(id="3", name="Track3", artist="Artist3", album="Album3"),
    ]

    # Launch concurrent writes
    await asyncio.gather(
        service.save_snapshot(tracks1),
        service.save_snapshot(tracks2),
    )

    # Verify writes completed (each write has start/end pair)
    assert len(execution_order) == 4
    # Verify writes were serialized: one must complete before other starts
    # Pattern should be: start_X, end_X, start_Y, end_Y (serialized)
    # NOT: start_X, start_Y, end_X, end_Y (interleaved)
    assert execution_order[0].startswith("start_")
    assert execution_order[1].startswith("end_")
    assert execution_order[2].startswith("start_")
    assert execution_order[3].startswith("end_")


def test_service_has_write_lock(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Test that LibrarySnapshotService has _write_lock attribute."""
    import asyncio

    config = _make_config(tmp_path_factory)
    service = LibrarySnapshotService(config, logging.getLogger("test.snapshot"))

    assert hasattr(service, "_write_lock")
    assert isinstance(service._write_lock, asyncio.Lock)
