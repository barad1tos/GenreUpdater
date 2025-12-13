"""Tests for LibrarySnapshotService."""

from __future__ import annotations

import gzip
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.cache.snapshot import (
    DELTA_MAX_AGE,
    DELTA_MAX_TRACKED_IDS,
    GZIP_SUFFIX,
    JSON_SUFFIX,
    LibraryCacheMetadata,
    LibraryDeltaCache,
    LibrarySnapshotService,
)
from core.models.track_models import TrackDict


class MockAppleScriptClient:
    """Mock AppleScript client for testing that conforms to the protocol.

    Note: Parameters match the protocol signature for keyword argument compatibility.
    Unused parameters are assigned to _ to indicate intentional non-use.
    """

    def __init__(self) -> None:
        """Initialize mock client."""
        self.apple_scripts_dir: str | None = "/mock/scripts"
        self._fetch_all_track_ids_result: list[str] | None = []
        self._run_script_result: str | None = None

    async def initialize(self) -> None:
        """Initialize mock."""

    async def run_script(
        self,
        script_name: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
        context_artist: str | None = None,
        context_album: str | None = None,
        context_track: str | None = None,
        label: str | None = None,
    ) -> str | None:
        """Run script mock."""
        _ = (script_name, arguments, timeout, context_artist, context_album, context_track, label)
        return self._run_script_result

    @staticmethod
    async def fetch_tracks_by_ids(
        track_ids: list[str],
        batch_size: int = 1000,
        timeout: float | None = None,
    ) -> list[dict[str, str]]:
        """Fetch tracks by IDs mock."""
        _ = (track_ids, batch_size, timeout)
        return []

    async def fetch_all_track_ids(self, timeout: float | None = None) -> list[str]:
        """Fetch all track IDs mock."""
        _ = timeout
        return self._fetch_all_track_ids_result or []

    def set_fetch_all_track_ids_result(self, result: list[str] | None) -> None:
        """Set result for fetch_all_track_ids."""
        self._fetch_all_track_ids_result = result

    def set_run_script_result(self, result: str | None) -> None:
        """Set result for run_script."""
        self._run_script_result = result


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


def _create_failing_temp_file_mock(temp_file_path: Path) -> MagicMock:
    """Create a mock for tempfile.NamedTemporaryFile that fails on write.

    Args:
        temp_file_path: Path where the mock temp file should be created.

    Returns:
        MagicMock configured to simulate a write failure.
    """
    mock_file = MagicMock()
    mock_file.__enter__ = MagicMock(return_value=mock_file)
    mock_file.__exit__ = MagicMock(return_value=False)
    mock_file.name = str(temp_file_path)
    temp_file_path.write_bytes(b"temp")
    mock_file.write = MagicMock(side_effect=OSError("Write failed"))
    return mock_file


@pytest.fixture
def sample_tracks() -> list[TrackDict]:
    """Create sample tracks."""
    return [
        TrackDict(id="1", name="Track 1", artist="Artist", album="Album", genre="Rock", year="2020"),
        TrackDict(id="2", name="Track 2", artist="Artist", album="Album", genre="Rock", year="2020"),
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

    def tracking_write(path: Path, data: bytes) -> None:
        """Track write execution order for concurrency testing."""
        execution_order.append(f"start_{len(data)}")
        original_write(path, data)
        execution_order.append(f"end_{len(data)}")

    service._write_bytes_atomic = tracking_write  # type: ignore[method-assign,assignment]

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


# ========================= Load Snapshot Tests =========================


class TestLoadSnapshotEdgeCases:
    """Tests for load_snapshot edge cases."""

    @pytest.mark.asyncio
    async def test_load_snapshot_returns_none_when_file_not_exists(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when snapshot file doesn't exist."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        result = await service.load_snapshot()
        assert result is None

    @pytest.mark.asyncio
    async def test_load_snapshot_raises_on_invalid_payload_type(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should raise TypeError when payload is not a list."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        snapshot_path = service._snapshot_path
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text('{"invalid": "format"}', encoding="utf-8")

        with pytest.raises(TypeError, match="must be a list"):
            await service.load_snapshot()

    @pytest.mark.asyncio
    async def test_load_snapshot_raises_on_invalid_track_entry(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should raise TypeError when track entry is invalid type."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        snapshot_path = service._snapshot_path
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text('[123, "not a dict"]', encoding="utf-8")

        with pytest.raises(TypeError, match="Invalid snapshot entry type"):
            await service.load_snapshot()


# ========================= Is Snapshot Valid Tests =========================


class TestIsSnapshotValid:
    """Tests for is_snapshot_valid method."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_metadata(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when metadata doesn't exist."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        result = await service.is_snapshot_valid()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_version_mismatch(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when version doesn't match."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        now = datetime.now()
        metadata = LibraryCacheMetadata(
            last_full_scan=now,
            library_mtime=now,
            track_count=10,
            snapshot_hash="abc",
            version="0.5",
        )
        await service.update_snapshot_metadata(metadata)

        result = await service.is_snapshot_valid()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_library_not_found(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when music library path doesn't exist."""
        config = _make_config(tmp_path_factory)
        config["music_library_path"] = "/nonexistent/path/library.musiclibrary"
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        now = datetime.now()
        metadata = LibraryCacheMetadata(
            last_full_scan=now,
            library_mtime=now,
            track_count=10,
            snapshot_hash="abc",
        )
        await service.update_snapshot_metadata(metadata)

        result = await service.is_snapshot_valid()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_library_unchanged(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return True when library hasn't changed."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        snapshot_hash = await service.save_snapshot(tracks)

        library_mtime = await service.get_library_mtime()
        now = datetime.now()
        metadata = LibraryCacheMetadata(
            last_full_scan=now,
            library_mtime=library_mtime,
            track_count=len(tracks),
            snapshot_hash=snapshot_hash,
        )
        await service.update_snapshot_metadata(metadata)

        result = await service.is_snapshot_valid()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_expired_and_library_changed(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when snapshot expired and library changed."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        snapshot_hash = await service.save_snapshot(tracks)

        # Set last_full_scan to MUCH older than max_age (100 hours > 24 hours default)
        very_old_time = datetime.now() - timedelta(hours=100)
        library_mtime = await service.get_library_mtime()
        metadata = LibraryCacheMetadata(
            last_full_scan=very_old_time,
            # Set metadata's library_mtime to be OLDER than current,
            # so library appears changed
            library_mtime=library_mtime - timedelta(hours=2),
            track_count=len(tracks),
            snapshot_hash=snapshot_hash,
        )
        await service.update_snapshot_metadata(metadata)

        result = await service.is_snapshot_valid()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_snapshot_file_missing(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when snapshot file is missing but metadata exists."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        library_mtime = await service.get_library_mtime()
        now = datetime.now()
        metadata = LibraryCacheMetadata(
            last_full_scan=now,
            library_mtime=library_mtime,
            track_count=10,
            snapshot_hash="abc",
        )
        await service.update_snapshot_metadata(metadata)

        result = await service.is_snapshot_valid()
        assert result is False


# ========================= Get Snapshot Metadata Tests =========================


class TestGetSnapshotMetadata:
    """Tests for get_snapshot_metadata method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_not_exists(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when metadata file doesn't exist."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        result = await service.get_snapshot_metadata()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_parse_error(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when metadata file is corrupted."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        service._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        service._metadata_path.write_text("not valid json", encoding="utf-8")

        result = await service.get_snapshot_metadata()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_missing_key(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when required key is missing."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        service._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        service._metadata_path.write_text('{"version": "1.0"}', encoding="utf-8")

        result = await service.get_snapshot_metadata()
        assert result is None


# ========================= Load Delta Tests =========================


class TestLoadDeltaEdgeCases:
    """Tests for load_delta edge cases."""

    @pytest.mark.asyncio
    async def test_returns_none_when_delta_disabled(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when delta is disabled."""
        config = _make_config(tmp_path_factory)
        config["caching"]["library_snapshot"]["delta_enabled"] = False
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        result = await service.load_delta()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_delta_file_not_exists(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when delta file doesn't exist."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        result = await service.load_delta()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_parse_error(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when delta file is corrupted."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        service._delta_path.parent.mkdir(parents=True, exist_ok=True)
        service._delta_path.write_text("not json", encoding="utf-8")

        result = await service.load_delta()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_delta_should_reset(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when delta exceeds limits."""
        import json

        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        # Write delta file DIRECTLY (bypass save_delta which resets the delta)
        old_time = datetime.now() - DELTA_MAX_AGE - timedelta(days=1)
        delta_data = {
            "last_run": old_time.isoformat(),
            "processed_track_ids": [],
            "field_hashes": {},
            "tracked_since": old_time.isoformat(),
        }
        service._delta_path.parent.mkdir(parents=True, exist_ok=True)
        service._delta_path.write_text(json.dumps(delta_data), encoding="utf-8")

        result = await service.load_delta()
        assert result is None


# ========================= Save Delta Tests =========================


class TestSaveDeltaEdgeCases:
    """Tests for save_delta edge cases."""

    @pytest.mark.asyncio
    async def test_does_nothing_when_delta_disabled(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return early when delta is disabled."""
        config = _make_config(tmp_path_factory)
        config["caching"]["library_snapshot"]["delta_enabled"] = False
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        delta = LibraryDeltaCache(
            last_run=datetime.now(),
            processed_track_ids={"1", "2"},
            field_hashes={},
        )
        await service.save_delta(delta)

        assert not service._delta_path.exists()

    @pytest.mark.asyncio
    async def test_resets_delta_when_should_reset(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should reset delta when it exceeds limits."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        old_time = datetime.now() - DELTA_MAX_AGE - timedelta(days=1)
        delta = LibraryDeltaCache(
            last_run=old_time,
            processed_track_ids={str(i) for i in range(100)},
            field_hashes={},
            tracked_since=old_time,
        )
        await service.save_delta(delta)

        assert delta.processed_track_ids == set()


# ========================= Get Library Mtime Tests =========================


class TestGetLibraryMtime:
    """Tests for get_library_mtime method."""

    @pytest.mark.asyncio
    async def test_raises_when_path_not_configured(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should raise FileNotFoundError when path is not configured."""
        config = _make_config(tmp_path_factory)
        del config["music_library_path"]
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        with pytest.raises(FileNotFoundError, match="music_library_path not configured"):
            await service.get_library_mtime()

    @pytest.mark.asyncio
    async def test_raises_when_file_not_exists(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should raise FileNotFoundError when file doesn't exist."""
        config = _make_config(tmp_path_factory)
        config["music_library_path"] = "/nonexistent/path.musiclibrary"
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        with pytest.raises(FileNotFoundError):
            await service.get_library_mtime()

    @pytest.mark.asyncio
    async def test_returns_mtime_for_existing_file(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return mtime for existing file."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        mtime = await service.get_library_mtime()
        assert isinstance(mtime, datetime)


# ========================= Parse Fetch Tracks Output Tests =========================


class TestParseFetchTracksOutput:
    """Tests for _parse_fetch_tracks_output method."""

    def test_parses_valid_output(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should parse valid AppleScript output."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        raw_output = (
            "1\x1eName\x1eArtist\x1eAlbum Artist\x1eAlbum\x1eRock\x1e"
            "2024-01-01\x1eplayed\x1e2020\x1e2020\x1e\x1d"
            "2\x1eName2\x1eArtist2\x1e\x1eAlbum2\x1ePop\x1e"
            "2024-01-02\x1eplayed\x1e2021\x1e2021\x1e\x1d"
        )

        result = service._parse_fetch_tracks_output(raw_output)

        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[0]["artist"] == "Artist"
        assert result[1]["id"] == "2"
        assert result[1]["genre"] == "Pop"

    def test_skips_empty_lines(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should skip empty lines."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        raw_output = "\x1d\x1d\x1d"

        result = service._parse_fetch_tracks_output(raw_output)
        assert result == []

    def test_skips_lines_with_insufficient_fields(self, tmp_path_factory: pytest.TempPathFactory, caplog: pytest.LogCaptureFixture) -> None:
        """Should skip lines with insufficient fields and log warning."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        raw_output = "1\x1eName\x1eArtist\x1d"

        with caplog.at_level(logging.WARNING):
            result = service._parse_fetch_tracks_output(raw_output)

        assert result == []
        assert "insufficient fields" in caplog.text

    def test_parses_mixed_valid_and_invalid_lines(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should parse valid lines and skip invalid ones."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        raw_output = "1\x1eName\x1eArtist\x1eAlbum Artist\x1eAlbum\x1eRock\x1e2024-01-01\x1eplayed\x1e2020\x1e2020\x1e\x1dinvalid\x1eline\x1d"

        result = service._parse_fetch_tracks_output(raw_output)
        assert len(result) == 1
        assert result[0]["id"] == "1"


# ========================= Compute Smart Delta Tests =========================


class TestComputeSmartDelta:
    """Tests for compute_smart_delta method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_snapshot(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when no snapshot exists."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        mock_client = MockAppleScriptClient()
        result = await service.compute_smart_delta(mock_client)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_fetch_ids_returns_empty(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return None when fetching IDs returns empty list."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        await service.save_snapshot(tracks)

        mock_client = MockAppleScriptClient()
        mock_client.set_fetch_all_track_ids_result([])

        result = await service.compute_smart_delta(mock_client)
        assert result is None

    @pytest.mark.asyncio
    async def test_detects_new_tracks(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should detect new track IDs."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        await service.save_snapshot(tracks)

        mock_client = MockAppleScriptClient()
        mock_client.set_fetch_all_track_ids_result(["1", "2", "3"])

        result = await service.compute_smart_delta(mock_client)
        assert result is not None
        assert "3" in result.new_ids

    @pytest.mark.asyncio
    async def test_detects_removed_tracks(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should detect removed track IDs."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        await service.save_snapshot(tracks)

        mock_client = MockAppleScriptClient()
        mock_client.set_fetch_all_track_ids_result(["1"])

        result = await service.compute_smart_delta(mock_client)
        assert result is not None
        assert "2" in result.removed_ids

    @pytest.mark.asyncio
    async def test_skips_updated_detection_by_default(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should skip updated detection when not using force mode."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        await service.save_snapshot(tracks)

        mock_client = MockAppleScriptClient()
        mock_client.set_fetch_all_track_ids_result(["1", "2"])

        result = await service.compute_smart_delta(mock_client)
        assert result is not None
        assert result.updated_ids == []

    @pytest.mark.asyncio
    async def test_force_mode_detects_updated_tracks(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should detect updated tracks in force mode."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        await service.save_snapshot(tracks)

        now = datetime.now()
        metadata = LibraryCacheMetadata(
            last_full_scan=now,
            library_mtime=now,
            track_count=len(tracks),
            snapshot_hash="abc",
            last_force_scan_time=now.isoformat(),
        )
        await service.update_snapshot_metadata(metadata)

        mock_client = MockAppleScriptClient()
        mock_client.set_fetch_all_track_ids_result(["1", "2"])

        raw_output = (
            "1\x1eUpdated Name\x1eArtist A\x1e\x1eAlbum A\x1eRock\x1e"
            "2024-01-01\x1eplayed\x1e2020\x1e2020\x1e\x1d"
            "2\x1eBeta\x1eArtist B\x1e\x1eAlbum B\x1e\x1e"
            "2024-01-01\x1eplayed\x1e\x1e\x1e\x1d"
        )
        mock_client.set_run_script_result(raw_output)

        result = await service.compute_smart_delta(mock_client, force=True)
        assert result is not None
        assert "1" in result.updated_ids


# ========================= Detect Updated Tracks Tests =========================


class TestDetectUpdatedTracks:
    """Tests for _detect_updated_tracks method."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_raw_tracks(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return empty list when no raw tracks."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        await service.save_snapshot(tracks)
        snapshot_map = {str(t.id): t for t in tracks}

        mock_client = MockAppleScriptClient()
        mock_client.set_run_script_result("")

        now = datetime.now()
        metadata = LibraryCacheMetadata(
            last_full_scan=now,
            library_mtime=now,
            track_count=len(tracks),
            snapshot_hash="abc",
        )
        await service.update_snapshot_metadata(metadata)

        with patch("services.cache.snapshot.spinner"):
            result = await service._detect_updated_tracks(
                mock_client,
                {"1", "2"},
                {"1", "2"},
                snapshot_map,
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_logs_warning_on_parse_failure(self, tmp_path_factory: pytest.TempPathFactory, caplog: pytest.LogCaptureFixture) -> None:
        """Should log warning when track parsing fails."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        tracks = _make_tracks()
        await service.save_snapshot(tracks)
        snapshot_map = {str(t.id): t for t in tracks}

        mock_client = MockAppleScriptClient()
        raw_output = "invalid\x1eline\x1d"
        mock_client.set_run_script_result(raw_output)

        now = datetime.now()
        metadata = LibraryCacheMetadata(
            last_full_scan=now,
            library_mtime=now,
            track_count=len(tracks),
            snapshot_hash="abc",
        )
        await service.update_snapshot_metadata(metadata)

        with patch("services.cache.snapshot.spinner"), caplog.at_level(logging.WARNING):
            result = await service._detect_updated_tracks(
                mock_client,
                {"1", "2"},
                {"1", "2"},
                snapshot_map,
            )

        assert result == []


# ========================= Is Enabled / Is Delta Enabled Tests =========================


class TestIsEnabled:
    """Tests for is_enabled and is_delta_enabled methods."""

    def test_is_enabled_returns_true(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return True when enabled."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        assert service.is_enabled() is True

    def test_is_enabled_returns_false(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when disabled."""
        config = _make_config(tmp_path_factory)
        config["caching"]["library_snapshot"]["enabled"] = False
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        assert service.is_enabled() is False

    def test_is_delta_enabled_returns_true(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return True when both enabled."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        assert service.is_delta_enabled() is True

    def test_is_delta_enabled_returns_false_when_disabled(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when delta disabled."""
        config = _make_config(tmp_path_factory)
        config["caching"]["library_snapshot"]["delta_enabled"] = False
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        assert service.is_delta_enabled() is False

    def test_is_delta_enabled_returns_false_when_main_disabled(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when main snapshot disabled."""
        config = _make_config(tmp_path_factory)
        config["caching"]["library_snapshot"]["enabled"] = False
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        assert service.is_delta_enabled() is False


# ========================= Deserialize Tracks Tests =========================


class TestDeserializeTracks:
    """Tests for _deserialize_tracks static method."""

    def test_raises_on_non_list_payload(self) -> None:
        """Should raise TypeError when payload is not a list."""
        with pytest.raises(TypeError, match="must be a list"):
            LibrarySnapshotService._deserialize_tracks("not a list")

    def test_passes_through_track_dict_instances(self) -> None:
        """Should pass through TrackDict instances."""
        track = TrackDict(id="1", name="Test", artist="Artist", album="Album")
        result = LibrarySnapshotService._deserialize_tracks([track])
        assert result == [track]

    def test_converts_mapping_to_track_dict(self) -> None:
        """Should convert mapping to TrackDict."""
        data = {"id": "1", "name": "Test", "artist": "Artist", "album": "Album"}
        result = LibrarySnapshotService._deserialize_tracks([data])
        assert len(result) == 1
        assert result[0].id == "1"

    def test_raises_on_invalid_mapping(self) -> None:
        """Should raise TypeError on invalid mapping."""
        data = {"invalid": "data"}
        with pytest.raises(TypeError, match="Invalid snapshot entry"):
            LibrarySnapshotService._deserialize_tracks([data])

    def test_raises_on_invalid_entry_type(self) -> None:
        """Should raise TypeError on invalid entry type."""
        with pytest.raises(TypeError, match="Invalid snapshot entry type"):
            LibrarySnapshotService._deserialize_tracks([123])


# ========================= Write Bytes Atomic Tests =========================


class TestWriteBytesAtomic:
    """Tests for _write_bytes_atomic method."""

    def test_writes_data_atomically(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should write data atomically."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        target_path = Path(config["logs_base_dir"]) / "test_file.txt"
        data = b"test data"

        service._write_bytes_atomic(target_path, data)

        assert target_path.exists()
        assert target_path.read_bytes() == data

    def test_cleans_up_temp_file_on_failure(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should clean up temp file on write failure."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        target_path = Path(config["logs_base_dir"]) / "test_file.txt"
        temp_file_path = target_path.parent / "temp_file"

        with patch("tempfile.NamedTemporaryFile") as mock_temp:
            mock_file = _create_failing_temp_file_mock(temp_file_path)
            mock_temp.return_value = mock_file

            with pytest.raises(OSError, match="Write failed"):
                service._write_bytes_atomic(target_path, b"data")


# ========================= Ensure Single Cache Format Tests =========================


class TestEnsureSingleCacheFormat:
    """Tests for _ensure_single_cache_format method."""

    def test_removes_plain_file_when_compress_enabled(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should remove plain file when compression is enabled."""
        config = _make_config(tmp_path_factory, compress=True)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        plain_path = service._base_cache_path.with_suffix(JSON_SUFFIX)
        plain_path.parent.mkdir(parents=True, exist_ok=True)
        plain_path.write_text("data", encoding="utf-8")

        service._ensure_single_cache_format()

        assert not plain_path.exists()

    def test_removes_compressed_file_when_compress_disabled(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should remove compressed file when compression is disabled."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        compressed_path = service._base_cache_path.with_suffix(GZIP_SUFFIX)
        compressed_path.parent.mkdir(parents=True, exist_ok=True)
        compressed_path.write_bytes(gzip.compress(b"data"))

        service._ensure_single_cache_format()

        assert not compressed_path.exists()

    def test_handles_removal_error_gracefully(self, tmp_path_factory: pytest.TempPathFactory, caplog: pytest.LogCaptureFixture) -> None:
        """Should handle removal errors gracefully."""
        config = _make_config(tmp_path_factory, compress=True)
        service = LibrarySnapshotService(config, logging.getLogger("test"))

        plain_path = service._base_cache_path.with_suffix(JSON_SUFFIX)
        plain_path.parent.mkdir(parents=True, exist_ok=True)
        plain_path.write_text("data", encoding="utf-8")

        # Patch Path.unlink at the pathlib module level
        original_unlink = Path.unlink

        def mock_unlink(path_instance: Path, missing_ok: bool = False) -> None:
            """Mock unlink that raises OSError for the target path."""
            _ = missing_ok
            if path_instance == plain_path:
                raise OSError("Permission denied")
            original_unlink(path_instance)

        with (
            patch.object(Path, "unlink", mock_unlink),
            caplog.at_level(logging.WARNING),
        ):
            service._ensure_single_cache_format()

        assert "Failed to remove" in caplog.text or "Permission denied" in caplog.text


# ========================= Resolve Cache File Path Tests =========================


class TestResolveCacheFilePath:
    """Tests for _resolve_cache_file_path static method."""

    def test_handles_gz_suffix(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should normalize .gz suffix to .json."""
        config = _make_config(tmp_path_factory)
        options = {"cache_file": "cache/snapshot.gz", "logs_base_dir": config["logs_base_dir"]}

        result = LibrarySnapshotService._resolve_cache_file_path(config, options)
        assert result.suffix == JSON_SUFFIX

    def test_adds_json_suffix_if_missing(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should add .json suffix if missing."""
        config = _make_config(tmp_path_factory)
        options = {"cache_file": "cache/snapshot", "logs_base_dir": config["logs_base_dir"]}

        result = LibrarySnapshotService._resolve_cache_file_path(config, options)
        assert result.suffix == JSON_SUFFIX

    def test_uses_absolute_path_as_is(self) -> None:
        """Should use absolute path as is."""
        config: dict[str, Any] = {}
        options = {"cache_file": "/absolute/path/snapshot.json"}

        result = LibrarySnapshotService._resolve_cache_file_path(config, options)
        assert result == Path("/absolute/path/snapshot.json")

    def test_uses_cwd_when_no_logs_base_dir(self) -> None:
        """Should use cwd when logs_base_dir not set."""
        config: dict[str, Any] = {}
        options: dict[str, Any] = {"cache_file": "cache/snapshot.json"}

        result = LibrarySnapshotService._resolve_cache_file_path(config, options)
        assert result.is_absolute()


# ========================= Resolve Music Library Path Tests =========================


class TestResolveMusicLibraryPath:
    """Tests for _resolve_music_library_path static method."""

    def test_returns_none_when_not_configured(self) -> None:
        """Should return None when path not configured."""
        result = LibrarySnapshotService._resolve_music_library_path({}, {})
        assert result is None

    def test_resolves_relative_path(self) -> None:
        """Should resolve relative path."""
        config = {"music_library_path": "relative/path.musiclibrary"}

        result = LibrarySnapshotService._resolve_music_library_path(config, {})
        assert result is not None
        assert result.is_absolute()

    def test_handles_resolve_error(self) -> None:
        """Should fallback to absolute() on resolve error."""
        config = {"music_library_path": "path/with\x00nullbyte.lib"}

        with patch.object(Path, "resolve", side_effect=OSError("Invalid path")):
            result = LibrarySnapshotService._resolve_music_library_path(config, {})

        assert result is not None


# ========================= Should Force Scan Tests =========================


class TestShouldForceScan:
    """Tests for should_force_scan method."""

    @pytest.mark.asyncio
    async def test_returns_true_when_force_flag(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return True when force flag is set."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        result, reason = await service.should_force_scan(force_flag=True)
        assert result is True
        assert "CLI --force flag" in reason

    @pytest.mark.asyncio
    async def test_returns_false_for_first_run(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False for first run."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        result, reason = await service.should_force_scan()
        assert result is False
        assert "first run" in reason

    @pytest.mark.asyncio
    async def test_returns_true_for_weekly_scan(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return True when 7+ days since last force scan."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        old_time = datetime.now() - timedelta(days=8)
        metadata = LibraryCacheMetadata(
            last_full_scan=old_time,
            library_mtime=old_time,
            track_count=10,
            snapshot_hash="abc",
            last_force_scan_time=old_time.isoformat(),
        )
        await service.update_snapshot_metadata(metadata)

        result, reason = await service.should_force_scan()
        assert result is True
        assert "weekly scan" in reason

    @pytest.mark.asyncio
    async def test_returns_false_for_recent_force_scan(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Should return False when force scan was recent."""
        config = _make_config(tmp_path_factory)
        service = LibrarySnapshotService(config, logging.getLogger("test"))
        await service.initialize()

        recent_time = datetime.now() - timedelta(days=2)
        metadata = LibraryCacheMetadata(
            last_full_scan=recent_time,
            library_mtime=recent_time,
            track_count=10,
            snapshot_hash="abc",
            last_force_scan_time=recent_time.isoformat(),
        )
        await service.update_snapshot_metadata(metadata)

        result, reason = await service.should_force_scan()
        assert result is False
        assert "fast mode" in reason


# ========================= Library Delta Cache Tests =========================


class TestLibraryDeltaCacheShouldReset:
    """Tests for LibraryDeltaCache.should_reset method."""

    def test_returns_true_when_exceeds_max_ids(self) -> None:
        """Should return True when processed IDs exceed limit."""
        delta = LibraryDeltaCache(
            last_run=datetime.now(),
            processed_track_ids={str(i) for i in range(DELTA_MAX_TRACKED_IDS)},
            field_hashes={},
        )
        assert delta.should_reset() is True

    def test_returns_true_when_exceeds_max_age(self) -> None:
        """Should return True when delta exceeds max age."""
        old_time = datetime.now() - DELTA_MAX_AGE - timedelta(days=1)
        delta = LibraryDeltaCache(
            last_run=old_time,
            processed_track_ids=set(),
            field_hashes={},
            tracked_since=old_time,
        )
        assert delta.should_reset() is True

    def test_returns_false_when_within_limits(self) -> None:
        """Should return False when within limits."""
        delta = LibraryDeltaCache(
            last_run=datetime.now(),
            processed_track_ids={"1", "2", "3"},
            field_hashes={},
            tracked_since=datetime.now(),
        )
        assert delta.should_reset() is False


class TestLibraryDeltaCacheAddProcessedIds:
    """Tests for LibraryDeltaCache.add_processed_ids method."""

    def test_adds_ids_to_set(self) -> None:
        """Should add IDs to processed set."""
        delta = LibraryDeltaCache(
            last_run=datetime.now(),
            processed_track_ids=set(),
            field_hashes={},
        )
        delta.add_processed_ids(["1", "2"])
        assert delta.processed_track_ids == {"1", "2"}

    def test_sets_tracked_since_on_first_add(self) -> None:
        """Should set tracked_since on first add."""
        delta = LibraryDeltaCache(
            last_run=datetime.now(),
            processed_track_ids=set(),
            field_hashes={},
            tracked_since=None,
        )
        delta.add_processed_ids(["1"])
        assert delta.tracked_since is not None

    def test_resets_when_exceeds_limit(self) -> None:
        """Should reset when exceeds limit."""
        delta = LibraryDeltaCache(
            last_run=datetime.now(),
            processed_track_ids={str(i) for i in range(DELTA_MAX_TRACKED_IDS)},
            field_hashes={},
        )
        delta.add_processed_ids(["new"])
        assert delta.processed_track_ids == {"new"}


# ========================= Metadata With Force Scan Time Tests =========================


class TestMetadataWithForceScanTime:
    """Tests for LibraryCacheMetadata with last_force_scan_time."""

    def test_serializes_force_scan_time(self) -> None:
        """Should serialize last_force_scan_time."""
        now = datetime.now()
        metadata = LibraryCacheMetadata(
            last_full_scan=now,
            library_mtime=now,
            track_count=10,
            snapshot_hash="abc",
            last_force_scan_time=now.isoformat(),
        )
        data = metadata.to_dict()
        assert data["last_force_scan_time"] == now.isoformat()

    def test_deserializes_force_scan_time(self) -> None:
        """Should deserialize last_force_scan_time."""
        now = datetime.now()
        data = {
            "last_full_scan": now.isoformat(),
            "library_mtime": now.isoformat(),
            "track_count": 10,
            "snapshot_hash": "abc",
            "last_force_scan_time": now.isoformat(),
        }
        metadata = LibraryCacheMetadata.from_dict(data)
        assert metadata.last_force_scan_time == now.isoformat()


# ========================= Parse Raw Track Tests =========================


class TestParseRawTrack:
    """Tests for _parse_raw_track static method."""

    def test_parses_complete_track(self) -> None:
        """Should parse complete track data."""
        raw = {
            "id": "123",
            "name": "Track Name",
            "artist": "Artist",
            "album_artist": "Album Artist",
            "album": "Album",
            "genre": "Rock",
            "date_added": "2024-01-01",
            "track_status": "played",
            "year": "2020",
            "release_year": "2020",
            "new_year": "2021",
        }
        result = LibrarySnapshotService._parse_raw_track(raw)
        assert result.id == "123"
        assert result.name == "Track Name"
        assert result.year == "2020"

    def test_handles_empty_year(self) -> None:
        """Should handle empty year value."""
        raw = {
            "id": "123",
            "name": "Track",
            "artist": "Artist",
            "album": "Album",
            "year": "",
        }
        result = LibrarySnapshotService._parse_raw_track(raw)
        assert result.year is None

    def test_handles_whitespace_year(self) -> None:
        """Should handle whitespace-only year."""
        raw = {
            "id": "123",
            "name": "Track",
            "artist": "Artist",
            "album": "Album",
            "year": "   ",
        }
        result = LibrarySnapshotService._parse_raw_track(raw)
        assert result.year is None

    def test_handles_missing_fields(self) -> None:
        """Should handle missing optional fields."""
        raw = {"id": "123"}
        result = LibrarySnapshotService._parse_raw_track(raw)
        assert result.id == "123"
        assert result.name == ""
        assert result.artist == ""


# ========================= Compute Snapshot Hash Tests =========================


class TestComputeSnapshotHash:
    """Tests for compute_snapshot_hash static method."""

    def test_computes_deterministic_hash(self) -> None:
        """Should compute deterministic hash."""
        payload = [{"id": "1", "name": "Track"}]
        hash1 = LibrarySnapshotService.compute_snapshot_hash(payload)
        hash2 = LibrarySnapshotService.compute_snapshot_hash(payload)
        assert hash1 == hash2

    def test_different_payloads_produce_different_hashes(self) -> None:
        """Should produce different hashes for different payloads."""
        payload1 = [{"id": "1", "name": "Track"}]
        payload2 = [{"id": "2", "name": "Track"}]
        hash1 = LibrarySnapshotService.compute_snapshot_hash(payload1)
        hash2 = LibrarySnapshotService.compute_snapshot_hash(payload2)
        assert hash1 != hash2
