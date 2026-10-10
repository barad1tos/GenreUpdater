"""Test for MusicUpdater pipeline snapshot functionality."""

import logging
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, seal

import pytest
from app.music_updater import MusicUpdater
from core.models.track_models import TrackDict
from core.retry_handler import DatabaseRetryHandler, RetryPolicy
from metrics.analytics import Analytics, LoggerContainer
from services.cache.snapshot import LibrarySnapshotService
from services.dependency_container import DependencyContainer
from tests.factories import create_test_app_config


class DummyAppleScriptClient:
    """Dummy AppleScript client for testing."""

    @staticmethod
    async def run_script(*_args: Any, **_kwargs: Any) -> str | None:
        """Mock run_script method."""
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

    async def fetch_tracks_in_batches(self, batch_size: int = 1000, skip_snapshot_check: bool = False) -> list[TrackDict]:
        """Mock fetch_tracks_in_batches method."""
        _ = skip_snapshot_check  # Unused in mock
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
        fresh: bool = False,
    ) -> tuple[list[TrackDict], list[dict[str, Any]]]:
        """Mock update_genres_by_artist_async method."""
        assert last_run_time is None
        assert force
        assert not fresh  # Test uses force=True but not fresh mode
        updated_tracks = [track.copy(genre="UpdatedGenre") for track in tracks]
        return updated_tracks, []


class FakeYearService:
    """Fake year service for testing."""

    def __init__(self, snapshot_manager: Any) -> None:
        """Initialize fake year service."""
        self._snapshot_manager = snapshot_manager

    async def update_all_years_with_logs(self, tracks: list[TrackDict], force: bool = False, fresh: bool = False) -> list[dict[str, Any]]:
        """Mock update_all_years_with_logs method."""
        assert force
        assert not fresh  # Test uses force=True but not fresh mode
        updated_tracks = [track.copy(year="2024") for track in tracks]
        self._snapshot_manager.update_tracks(updated_tracks)
        return []


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

    app_config = create_test_app_config(
        logs_base_dir=str(tmp_dir),
        batch_processing={"batch_size": 10},
        logging={
            "max_runs": 3,
            "main_log_file": "test.log",
            "analytics_log_file": "analytics.log",
            "csv_output_file": "csv/track_list.csv",
            "changes_report_file": "changes.json",
            "dry_run_report_file": "dryrun.json",
            "last_incremental_run_file": "lastrun.json",
            "pending_verification_file": "pending.json",
            "last_db_verify_log": "dbverify.log",
            "levels": {"console": "INFO", "main_file": "INFO", "analytics_file": "INFO"},
        },
    )

    analytics = Analytics(app_config, LoggerContainer(logger, logger, logger))

    # Create retry handler for testing
    retry_policy = RetryPolicy(
        max_retries=2,
        base_delay_seconds=0.01,
        max_delay_seconds=0.1,
        jitter_range=0.0,
        operation_timeout_seconds=30.0,
    )
    retry_handler = DatabaseRetryHandler(logger=logger, default_policy=retry_policy)

    # MusicUpdater's fetch and TrackCacheManager.update_snapshot (reached on persist) both return early when is_enabled() is False,
    # so a disabled service keeps this run on the full batch fetch and writes no snapshot
    snapshot_service = MagicMock(spec=LibrarySnapshotService)
    snapshot_service.is_enabled.return_value = False

    deps = MagicMock(spec_set=DependencyContainer)
    deps.configure_mock(
        app_config=app_config,
        config_path=tmp_dir / "config.yaml",
        console_logger=logger,
        error_logger=logger,
        analytics=analytics,
        analytics_logger=logger,
        ap_client=DummyAppleScriptClient(),
        cache_service=DummyCacheService(),
        library_snapshot_service=snapshot_service,
        pending_verification_service=DummyPendingVerificationService(),
        external_api_service=DummyExternalApiService(),
        retry_handler=retry_handler,
        dry_run=False,
        db_verify_logger=logger,
    )
    # Reading a dependency the test did not configure raises instead of returning a mock.
    # configure_mock adopts the unnamed snapshot_service as a child, so the seal covers it and a call past its is_enabled() gate raises.
    seal(deps)

    music_updater = MusicUpdater(deps)

    tracks = [
        TrackDict(id="1", name="Song A", artist="Artist", album="Album", genre="Old", year="2000"),
        TrackDict(id="2", name="Song B", artist="Artist", album="Album", genre="Old", year="2001"),
    ]

    fake_tp = FakeTrackProcessor(tracks)
    fake_genre_manager = FakeGenreManager()
    fake_db_verifier = FakeDatabaseVerifier()

    monkeypatch.setattr(music_updater, "track_processor", fake_tp)
    monkeypatch.setattr(music_updater, "genre_manager", fake_genre_manager)
    monkeypatch.setattr(music_updater, "database_verifier", fake_db_verifier)

    # Create FakeYearService with the music_updater's snapshot_manager
    fake_year_service = FakeYearService(music_updater.snapshot_manager)
    monkeypatch.setattr(music_updater, "year_service", fake_year_service)

    captured: dict[str, Any] = {}

    async def fake_sync(
        all_current_tracks: list[TrackDict],
        file_path: str,
        *,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Fake sync function for testing."""
        _ = (console_logger, error_logger)
        captured["tracks"] = all_current_tracks
        captured["path"] = file_path

    monkeypatch.setattr("app.music_updater.sync_track_list_with_current", fake_sync)

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
