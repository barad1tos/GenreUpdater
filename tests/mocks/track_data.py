"""Mock data generators for track-related testing."""

from __future__ import annotations

from src.core.models.track_models import TrackDict


# NOTE: DummyTrackSummary class removed after track_delta_service.py refactoring
# TrackSummary no longer exists - using TrackDict directly now


class DummyTrackData:
    """Factory for TrackDict test data."""

    @staticmethod
    def create(
        track_id: str = "12345",
        name: str = "Test Track",
        artist: str = "Test Artist",
        album: str = "Test Album",
        genre: str | None = "Rock",
        date_added: str | None = "2024-01-01 12:00:00",
        year: str | None = "2024",
        last_modified: str | None = "2024-01-01 12:00:00",
        track_status: str | None = "subscription",
    ) -> TrackDict:
        """Create a basic TrackDict with sensible defaults."""
        return TrackDict(
            id=track_id,
            name=name,
            artist=artist,
            album=album,
            genre=genre,
            date_added=date_added,
            year=year,
            last_modified=last_modified,
            track_status=track_status,
        )

    @staticmethod
    def missing_genre_track() -> TrackDict:
        """Create a track that needs genre processing."""
        return DummyTrackData.create(
            track_id="no_genre_123",
            name="Unknown Genre Track",
            genre="",  # Missing genre
        )

    @staticmethod
    def prerelease_track() -> TrackDict:
        """Create a track with prerelease status."""
        return DummyTrackData.create(
            track_id="prerelease_456",
            name="Prerelease Track",
            date_added="2024-12-01 12:00:00",  # Recent
            track_status="prerelease",
        )

    @staticmethod
    def batch_tracks(count: int = 5) -> list[TrackDict]:
        """Create a batch of tracks for testing."""
        return [
            DummyTrackData.create(
                track_id=f"batch_{i}",
                name=f"Track {i}",
                artist=f"Artist {i // 2}",  # Multiple tracks per artist
            )
            for i in range(count)
        ]


class FakeTrackMap:
    """Fake in-memory track storage for testing."""

    def __init__(self, initial_tracks: list[TrackDict] | None = None) -> None:
        """Initialize with optional tracks."""
        self._tracks: dict[str, TrackDict] = {}
        if initial_tracks:
            for track in initial_tracks:
                if track_id := str(track.id):
                    self._tracks[track_id] = track

    def get(self, track_id: str) -> TrackDict | None:
        """Get track by ID."""
        return self._tracks.get(track_id)

    def add(self, track: TrackDict) -> None:
        """Add or update track."""
        if track_id := str(track.id):
            self._tracks[track_id] = track

    def remove(self, track_id: str) -> bool:
        """Remove track by ID. Returns True if removed."""
        return self._tracks.pop(track_id, None) is not None

    def all_tracks(self) -> list[TrackDict]:
        """Get all tracks as list."""
        return list(self._tracks.values())

    def to_dict(self) -> dict[str, TrackDict]:
        """Get internal dict (for compute_track_delta compatibility)."""
        return self._tracks.copy()

    @property
    def count(self) -> int:
        """Number of tracks stored."""
        return len(self._tracks)


class MockCSVLoader:
    """Mock CSV operations for testing incremental logic."""

    def __init__(self, tracks_to_return: list[TrackDict] | None = None) -> None:
        """Initialize with tracks that should be 'loaded' from CSV."""
        self.tracks_to_return = tracks_to_return or []
        self.load_called = False
        self.csv_path_requested: str | None = None

    def load_track_list(self, csv_path: str) -> dict[str, TrackDict]:
        """Mock implementation of load_track_list from reports module."""
        self.load_called = True
        self.csv_path_requested = csv_path

        # Convert list to dict with track_id as key
        result: dict[str, TrackDict] = {}
        for track in self.tracks_to_return:
            if track_id := str(track.id):
                result[track_id] = track

        return result
