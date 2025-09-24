"""Test for MusicUpdater pipeline snapshot functionality."""

import logging
from datetime import datetime
from typing import Any

import pytest

from src.core.music_updater import MusicUpdater
from src.utils.data.models import TrackDict
from src.utils.monitoring.analytics import Analytics, LoggerContainer


class MockDependencyContainer:
    """Mock dependency container for testing."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize mock container with given attributes."""
        for key, value in kwargs.items():
            setattr(self, key, value)


class DummyAppleScriptClient:
    """Dummy AppleScript client for testing."""

    @staticmethod
    async def run_script(*_args: Any, **_kwargs: Any) -> str | None:
        """Mock run_script method."""
        return ""

    @staticmethod
    async def run_script_code(*_args: Any, **_kwargs: Any) -> str | None:
        """Mock run_script_code method."""
        return ""


class DummyCacheService:
    """Dummy cache service for testing."""

    def __init__(self) -> None:
        """Initialize dummy cache."""
        self.storage: dict[str, Any] = {}

    async def set_async(self, key_data: str, value: Any, _ttl: int | None = None) -> None:
        """Mock set_async method."""
        self.storage[key_data] = value

    async def get_async(self, key_data: str) -> Any | None:
        """Mock get_async method."""
        return self.storage.get(key_data)

    @staticmethod
    def generate_album_key(artist: str, album: str) -> str:
        """Generate a simplified album key for tests."""
        return f"{artist.lower()}::{album.lower()}"


class DummyPendingVerificationService:
    """Dummy pending verification service for testing."""

    @staticmethod
    async def mark_for_verification(*_args: Any, **_kwargs: Any) -> None:
        """Mock mark_for_verification method."""

    @staticmethod
    async def generate_problematic_albums_report(*_args: Any, **_kwargs: Any) -> int:
        """Mock generate_problematic_albums_report method."""
        return 0


class DummyExternalApiService:
    """Dummy external API service for testing."""

    @staticmethod
    async def initialize() -> None:
        """Mock initialize method."""


class FakeTrackProcessor:
    """Fake track processor for testing."""

    def __init__(self, tracks: list[TrackDict]) -> None:
        """Initialize fake track processor."""
        self.tracks = tracks
        self.fetch_batches_calls = 0
        self.fetch_async_calls: list[tuple[Any, ...]] = []

    @staticmethod
    def set_dry_run_context(*_args: Any, **_kwargs: Any) -> None:
        """Mock set_dry_run_context method."""

    async def fetch_tracks_in_batches(self, batch_size: int = 1000) -> list[TrackDict]:
        """Mock fetch_tracks_in_batches method."""
        self.fetch_batches_calls += 1
        assert batch_size == 10
        return self.tracks

    async def fetch_tracks_async(
        self,
        artist: str | None = None,
        force_refresh: bool = False,
        dry_run_test_tracks: list[TrackDict] | None = None,
        ignore_test_filter: bool = False,
    ) -> list[TrackDict]:
        """Mock fetch_tracks_async method."""
        self.fetch_async_calls.append((artist, force_refresh, dry_run_test_tracks, ignore_test_filter))
        return self.tracks

    @staticmethod
    async def update_track_async(*_args: Any, **_kwargs: Any) -> bool:
        """Mock update_track_async method."""
        return True


class FakeGenreManager:
    """Fake genre manager for testing."""

    @staticmethod
    async def update_genres_by_artist_async(
        tracks: list[TrackDict],
        last_run_time: datetime | None = None,
        force: bool = False,
    ) -> tuple[list[TrackDict], list[dict[str, Any]]]:
        """Mock update_genres_by_artist_async method."""
        assert last_run_time is None
        assert force is True
        updated_tracks = [track.copy(genre="UpdatedGenre") for track in tracks]
        return updated_tracks, []


class FakeYearRetriever:
    """Fake year retriever for testing."""

    def __init__(self) -> None:
        """Initialize fake year retriever."""
        self._updated_tracks: list[TrackDict] = []

    async def process_album_years(self, tracks: list[TrackDict], force: bool = False) -> bool:
        """Mock process_album_years method."""
        assert force is True
        self._updated_tracks = [track.copy(year="2024") for track in tracks]
        return True

    def get_last_updated_tracks(self) -> list[TrackDict]:
        """Mock get_last_updated_tracks method."""
        return self._updated_tracks


class FakeDatabaseVerifier:
    """Fake database verifier for testing."""

    def __init__(self) -> None:
        """Initialize fake database verifier."""
        self.updated_last_run = False

    async def update_last_incremental_run(self, *_args: Any, **_kwargs: Any) -> None:
        """Mock update_last_incremental_run method."""
        self.updated_last_run = True


@pytest.mark.asyncio
async def test_main_pipeline_reuses_track_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    tmp_dir = tmp_path_factory.mktemp("logs")
    logger = logging.getLogger("test.music_updater.snapshot")
    logger.addHandler(logging.NullHandler())

    config: dict[str, Any] = {
        "logs_base_dir": str(tmp_dir),
        "logging": {"csv_output_file": "csv/track_list.csv"},
        "batch_processing": {"batch_size": 10},
        "development": {"test_artists": []},
        "analytics": {"duration_thresholds": {"short_max": 1, "medium_max": 5, "long_max": 10}},
    }

    analytics = Analytics(config, LoggerContainer(logger, logger, logger))
    deps = MockDependencyContainer(
        config=config,
        console_logger=logger,
        error_logger=logger,
        analytics=analytics,
        analytics_logger=logger,
        ap_client=DummyAppleScriptClient(),
        cache_service=DummyCacheService(),
        pending_verification_service=DummyPendingVerificationService(),
        external_api_service=DummyExternalApiService(),
        dry_run=False,
        year_updates_logger=logger,
        db_verify_logger=logger,
        logging_listener=None,
    )

    music_updater = MusicUpdater(deps)  # type: ignore[arg-type]

    tracks = [
        TrackDict(id="1", name="Song A", artist="Artist", album="Album", genre="Old", year="2000"),
        TrackDict(id="2", name="Song B", artist="Artist", album="Album", genre="Old", year="2001"),
    ]

    fake_tp = FakeTrackProcessor(tracks)
    fake_genre_manager = FakeGenreManager()
    fake_year_retriever = FakeYearRetriever()
    fake_db_verifier = FakeDatabaseVerifier()

    music_updater.track_processor = fake_tp  # type: ignore[assignment]
    music_updater.genre_manager = fake_genre_manager  # type: ignore[assignment]
    music_updater.year_retriever = fake_year_retriever  # type: ignore[assignment]
    music_updater.database_verifier = fake_db_verifier  # type: ignore[assignment]

    captured: dict[str, Any] = {}

    async def fake_sync(
        all_current_tracks: list[TrackDict],
        file_path: str,
        _cache_service: DummyCacheService,
        _console_logger: logging.Logger,
        _error_logger: logging.Logger,
        *,
        partial_sync: bool = True,
    ) -> None:
        """Fake sync function for testing."""
        captured["tracks"] = all_current_tracks
        captured["path"] = file_path
        captured["partial"] = partial_sync

    monkeypatch.setattr("src.core.music_updater.sync_track_list_with_current", fake_sync)

    await music_updater.run_main_pipeline(True)

    assert fake_tp.fetch_batches_calls == 1
    assert fake_tp.fetch_async_calls == []

    expected_path = tmp_dir / "csv" / "track_list.csv"
    assert captured.get("path") == str(expected_path)
    assert captured.get("tracks") is tracks
    assert all(track.genre == "UpdatedGenre" for track in tracks)
    assert all(track.year == "2024" for track in tracks)
    assert not hasattr(music_updater, "_pipeline_tracks_snapshot") or getattr(music_updater, "_pipeline_tracks_snapshot", None) is None
    assert fake_db_verifier.updated_last_run is True
