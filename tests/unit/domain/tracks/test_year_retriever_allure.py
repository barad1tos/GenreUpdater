"""Enhanced YearRetriever tests with Allure reporting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import allure
import pytest
from src.domain.tracks import year_retriever
from src.domain.tracks.year_retriever import YearRetriever, is_empty_year
from src.shared.data.models import TrackDict
from src.shared.data.protocols import (
    CacheServiceProtocol,
    ExternalApiServiceProtocol,
    PendingVerificationServiceProtocol,
)

from tests.mocks.csv_mock import MockAnalytics, MockLogger  # noqa: TID252
from tests.mocks.protocol_mocks import (
    MockCacheService,
    MockExternalApiService,
    MockPendingVerificationService,
)
from tests.mocks.track_data import DummyTrackData  # noqa: TID252


@allure.epic("Music Genre Updater")
@allure.feature("Year Retrieval")
class TestYearRetrieverAllure:
    """Enhanced tests for YearRetriever with Allure reporting."""

    @staticmethod
    def create_year_retriever(
        track_processor: Any = None,
        cache_service: CacheServiceProtocol | None = None,
        external_api: ExternalApiServiceProtocol | None = None,
        pending_verification: PendingVerificationServiceProtocol | None = None,
        config: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> YearRetriever:
        """Create a YearRetriever instance for testing."""
        if track_processor is None:
            track_processor = MagicMock()
            track_processor.update_track_async = AsyncMock(return_value=True)

        if cache_service is None:
            cache_service = MockCacheService()

        if external_api is None:
            external_api = MockExternalApiService()

        if pending_verification is None:
            pending_verification = MockPendingVerificationService()

        console_logger = MockLogger()
        error_logger = MockLogger()
        analytics = MockAnalytics()

        test_config = config or {"year_retrieval": {"api_timeout": 30, "processing": {"batch_size": 50}, "retry_attempts": 3}}

        return YearRetriever(
            track_processor=track_processor,
            cache_service=cache_service,
            external_api=external_api,
            pending_verification=pending_verification,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=analytics,
            config=test_config,
            dry_run=dry_run,
        )

    @allure.story("Year Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should validate empty years correctly")
    @allure.description("Test identification of empty or invalid year values")
    @pytest.mark.parametrize(
        ("year_value", "expected"),
        [
            (None, True),
            ("", True),
            ("   ", True),
            ("0", False),
            ("1990", False),
            ("2024", False),
            (1990, False),
            # Note: integer 0 becomes "0" when stringified, which is not empty
        ],
    )
    def test_is_empty_year_parametrized(self, year_value: Any, expected: bool) -> None:
        """Test empty year detection with various inputs."""
        with allure.step(f"Testing empty year detection for: '{year_value}'"):
            result = is_empty_year(year_value)

            allure.attach(str(year_value), "Input Year", allure.attachment_type.TEXT)
            allure.attach(str(result), "Is Empty Result", allure.attachment_type.TEXT)
            allure.attach(str(expected), "Expected Result", allure.attachment_type.TEXT)

            assert result is expected

    @allure.story("Year Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should validate reasonable years correctly")
    @allure.description("Test reasonable year validation within acceptable ranges")
    @pytest.mark.parametrize(
        ("year", "expected"),
        [
            ("1899", False),  # Too old
            ("1900", True),  # Minimum valid
            ("1990", True),  # Normal year
            ("2024", True),  # Current era
            ("2025", True),  # Near future (allowed for new releases)
            ("2030", False),  # Too far in future
            ("abc", False),  # Non-numeric
            ("", False),  # Empty string
            ("0", False),  # Invalid zero
        ],
    )
    def test_is_reasonable_year_parametrized(self, year: str, expected: bool) -> None:
        """Test reasonable year validation with various inputs."""
        with allure.step(f"Testing reasonable year validation for: '{year}'"):
            # Access the private function through the imported module
            result = year_retriever._is_reasonable_year(year)  # noqa: SLF001

            current_year = datetime.now(UTC).year
            min_year = YearRetriever.MIN_VALID_YEAR
            max_year = current_year + 1

            allure.attach(year, "Input Year", allure.attachment_type.TEXT)
            allure.attach(str(result), "Is Reasonable Result", allure.attachment_type.TEXT)
            allure.attach(f"{min_year} - {max_year}", "Valid Year Range", allure.attachment_type.TEXT)

            assert result is expected

    @allure.story("Initialization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should initialize YearRetriever with all dependencies")
    @allure.description("Test that YearRetriever initializes correctly with required services")
    def test_year_retriever_initialization_comprehensive(self) -> None:
        """Test comprehensive YearRetriever initialization."""
        with allure.step("Setup mock dependencies"):
            mock_track_processor = MagicMock()
            mock_cache_service = cast(CacheServiceProtocol, cast(object, MockCacheService()))
            mock_external_api = cast(ExternalApiServiceProtocol, cast(object, MockExternalApiService()))
            mock_pending_verification = cast(PendingVerificationServiceProtocol, cast(object, MockPendingVerificationService()))

            config = {"year_retrieval": {"api_timeout": 45, "processing": {"batch_size": 100}, "retry_attempts": 5}}

        with allure.step("Initialize YearRetriever"):
            retriever = YearRetriever(
                track_processor=mock_track_processor,
                cache_service=mock_cache_service,
                external_api=mock_external_api,
                pending_verification=mock_pending_verification,
                console_logger=MockLogger(),
                error_logger=MockLogger(),
                analytics=MockAnalytics(),
                config=config,
                dry_run=True,
            )

        with allure.step("Verify initialization"):
            assert retriever.track_processor is mock_track_processor
            assert retriever.cache_service is mock_cache_service
            assert retriever.external_api is mock_external_api
            assert retriever.pending_verification is mock_pending_verification
            assert retriever.dry_run is True
            assert retriever.config == config

            # Verify constants
            assert YearRetriever.MIN_VALID_YEAR == 1900
            assert YearRetriever.PARITY_THRESHOLD == 2
            assert YearRetriever.DOMINANCE_MIN_SHARE == 0.6

            allure.attach("YearRetriever initialized successfully", "Initialization Result", allure.attachment_type.TEXT)
            allure.attach(str(config), "Configuration", allure.attachment_type.TEXT)

    @allure.story("Future Years Detection")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should extract future years from album tracks")
    @allure.description("Test extraction of future release years from track data")
    def test_extract_future_years_comprehensive(self) -> None:
        """Test future years extraction with various scenarios."""
        with allure.step("Create album tracks with mixed years"):
            current_year = datetime.now(UTC).year
            future_year_1 = current_year + 2
            future_year_2 = current_year + 3

            album_tracks = [
                DummyTrackData.create(track_id="1", name="Track 1", year=str(current_year)),
                DummyTrackData.create(track_id="2", name="Track 2", year=str(future_year_1)),
                DummyTrackData.create(track_id="3", name="Track 3", year=str(future_year_2)),
                DummyTrackData.create(track_id="4", name="Track 4", year=str(current_year - 1)),
                DummyTrackData.create(track_id="5", name="Track 5", year=""),  # Empty year
            ]

            allure.attach(f"Current year: {current_year}", "Reference Year", allure.attachment_type.TEXT)
            allure.attach(f"Future years: {future_year_1}, {future_year_2}", "Expected Future Years", allure.attachment_type.TEXT)

        with allure.step("Extract future years"):
            future_years = YearRetriever._extract_future_years(album_tracks)  # noqa: SLF001

        with allure.step("Verify future years extraction"):
            assert isinstance(future_years, list)
            assert len(future_years) == 2  # Two future years
            assert future_year_1 in future_years
            assert future_year_2 in future_years
            assert current_year not in future_years  # Current year should not be included

            allure.attach(str(future_years), "Extracted Future Years", allure.attachment_type.TEXT)

    @allure.story("Release Years Extraction")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should extract release years from album tracks")
    @allure.description("Test extraction of release years excluding empty values")
    def test_extract_release_years_comprehensive(self) -> None:
        """Test release years extraction with filtering."""
        with allure.step("Create album tracks with mixed year data"):
            album_tracks = [
                TrackDict(id="1", name="Track 1", artist="Test", album="Test", release_year="1990"),
                TrackDict(id="2", name="Track 2", artist="Test", album="Test", release_year="1990"),
                TrackDict(id="3", name="Track 3", artist="Test", album="Test", release_year="2000"),
                TrackDict(id="4", name="Track 4", artist="Test", album="Test", release_year=""),  # Empty
                TrackDict(id="5", name="Track 5", artist="Test", album="Test", release_year="   "),  # Whitespace
                TrackDict(id="6", name="Track 6", artist="Test", album="Test", release_year=None),  # None
            ]

        with allure.step("Extract release years"):
            release_years = YearRetriever._extract_release_years(album_tracks)  # noqa: SLF001

        with allure.step("Verify release years extraction"):
            assert isinstance(release_years, list)
            assert len(release_years) == 3  # Only valid years
            assert "1990" in release_years
            assert "2000" in release_years
            # Should contain two "1990" entries (not deduplicated)
            assert release_years.count("1990") == 2

            allure.attach(str(release_years), "Extracted Release Years", allure.attachment_type.TEXT)
            allure.attach(f"Valid years count: {len(release_years)}", "Results Summary", allure.attachment_type.TEXT)

    @allure.story("Track Grouping")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should group tracks by album correctly")
    @allure.description("Test grouping tracks by artist-album combinations")
    def test_group_tracks_by_album_comprehensive(self) -> None:
        """Test track grouping by album with various scenarios."""
        retriever = self.create_year_retriever()

        with allure.step("Create tracks from multiple albums"):
            tracks = [
                DummyTrackData.create(track_id="1", name="Song 1", artist="Artist A", album="Album 1"),
                DummyTrackData.create(track_id="2", name="Song 2", artist="Artist A", album="Album 1"),
                DummyTrackData.create(track_id="3", name="Song 3", artist="Artist A", album="Album 2"),
                DummyTrackData.create(track_id="4", name="Song 4", artist="Artist B", album="Album 1"),
                DummyTrackData.create(track_id="5", name="Song 5", artist="Artist B", album="Album 1"),
            ]

            allure.attach(f"Total tracks: {len(tracks)}", "Input Data", allure.attachment_type.TEXT)

        with allure.step("Group tracks by album"):
            grouped_albums = retriever._group_tracks_by_album(tracks)  # noqa: SLF001

        with allure.step("Verify album grouping"):
            assert isinstance(grouped_albums, dict)
            assert len(grouped_albums) == 3  # 3 unique artist-album combinations

            # Verify specific groups
            assert ("Artist A", "Album 1") in grouped_albums
            assert ("Artist A", "Album 2") in grouped_albums
            assert ("Artist B", "Album 1") in grouped_albums

            # Verify track counts in groups
            assert len(grouped_albums[("Artist A", "Album 1")]) == 2
            assert len(grouped_albums[("Artist A", "Album 2")]) == 1
            assert len(grouped_albums[("Artist B", "Album 1")]) == 2

            allure.attach(f"Grouped albums count: {len(grouped_albums)}", "Grouping Result", allure.attachment_type.TEXT)

            # Create summary for attachment
            summary = "\n".join([f"{artist} - {album}: {len(tracks)} tracks" for (artist, album), tracks in grouped_albums.items()])
            allure.attach(summary, "Album Groups Summary", allure.attachment_type.TEXT)

    @allure.story("Year Determination")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should determine album year from external API")
    @allure.description("Test album year determination using external API services")
    @pytest.mark.asyncio
    async def test_determine_album_year_from_api(self) -> None:
        """Test album year determination from external API."""
        with allure.step("Setup mock external API"):
            mock_external_api = MockExternalApiService()
            expected_year = "1995"
            mock_external_api.get_album_year_response = (expected_year, True)

        with allure.step("Create YearRetriever with mock API"):
            retriever = self.create_year_retriever(external_api=mock_external_api)

        with allure.step("Create test album tracks"):
            album_tracks = [
                DummyTrackData.create(track_id="1", name="Song 1", year=""),
                DummyTrackData.create(track_id="2", name="Song 2", year=""),
            ]

        with allure.step("Determine album year"):
            determined_year = await retriever._determine_album_year("Test Artist", "Test Album", album_tracks)  # noqa: SLF001

        with allure.step("Verify year determination"):
            assert determined_year == expected_year

            # Verify API was called correctly
            assert len(mock_external_api.get_album_year_calls) == 1
            assert mock_external_api.get_album_year_calls[0] == ("Test Artist", "Test Album", None)

            allure.attach(expected_year, "Determined Year", allure.attachment_type.TEXT)
            allure.attach("Test Artist", "Artist", allure.attachment_type.TEXT)
            allure.attach("Test Album", "Album", allure.attachment_type.TEXT)

    @allure.story("Collaboration Artists")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should normalize collaboration artist names")
    @allure.description("Test normalization of artist names with collaborations")
    @pytest.mark.parametrize(
        ("artist_input", "expected_output"),
        [
            ("Artist A feat. Artist B", "Artist A"),
            ("Artist A ft. Artist B", "Artist A"),
            ("Artist A feat Artist B", "Artist A"),  # Without dot
            ("Artist A & Artist B", "Artist A"),
            ("Artist A and Artist B", "Artist A"),
            ("Simple Artist", "Simple Artist"),
            ("Artist A with Artist B", "Artist A"),  # "with" is supported
        ],
    )
    def test_normalize_collaboration_artist(self, artist_input: str, expected_output: str) -> None:
        """Test collaboration artist normalization."""
        with allure.step(f"Normalizing artist: '{artist_input}'"):
            result = YearRetriever.normalize_collaboration_artist(artist_input)

        with allure.step("Verify normalization result"):
            assert result == expected_output

            allure.attach(artist_input, "Input Artist", allure.attachment_type.TEXT)
            allure.attach(result, "Normalized Artist", allure.attachment_type.TEXT)
            allure.attach(expected_output, "Expected Output", allure.attachment_type.TEXT)

    @allure.story("Track Updates")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should update album tracks with determined year")
    @allure.description("Test bulk update of album tracks with new year information")
    @pytest.mark.asyncio
    async def test_update_album_tracks_bulk_async_success(self) -> None:
        """Test successful bulk update of album tracks."""
        with allure.step("Setup mock track processor"):
            mock_track_processor = MagicMock()
            mock_track_processor.update_track_async = AsyncMock(return_value=True)

        with allure.step("Create YearRetriever with mock processor"):
            retriever = self.create_year_retriever(track_processor=mock_track_processor)

        with allure.step("Create album tracks needing year updates"):
            tracks_data = [
                ("track_001", DummyTrackData.create(track_id="track_001", name="Song 1", artist="Artist", album="Album", year="")),
                ("track_002", DummyTrackData.create(track_id="track_002", name="Song 2", artist="Artist", album="Album", year="")),
                ("track_003", DummyTrackData.create(track_id="track_003", name="Song 3", artist="Artist", album="Album", year="")),
            ]

            track_ids = [track_id for track_id, _ in tracks_data]

        with allure.step("Execute bulk update"):
            success_count, failed_count = await retriever.update_album_tracks_bulk_async(
                track_ids=track_ids, year="1990"
            )

        with allure.step("Verify bulk update results"):
            assert success_count == 3  # All tracks should succeed
            assert failed_count == 0  # No failures expected

            # Verify all tracks were updated
            assert mock_track_processor.update_track_async.call_count == 3

            allure.attach(str(success_count), "Successful Updates", allure.attachment_type.TEXT)
            allure.attach(str(failed_count), "Failed Updates", allure.attachment_type.TEXT)
            allure.attach("1990", "Applied Year", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle track update failures gracefully")
    @allure.description("Test error handling when track updates fail")
    @pytest.mark.asyncio
    async def test_update_album_tracks_with_failures(self) -> None:
        """Test handling of track update failures."""
        with allure.step("Setup failing track processor"):
            mock_track_processor = MagicMock()
            # First update succeeds, second fails, third succeeds
            mock_track_processor.update_track_async = AsyncMock(side_effect=[True, False, True])

        with allure.step("Create YearRetriever with failing processor"):
            retriever = self.create_year_retriever(track_processor=mock_track_processor)

        with allure.step("Create tracks for testing failure handling"):
            tracks_data = [
                ("success_001", DummyTrackData.create(track_id="success_001", name="Success 1")),
                ("failure_001", DummyTrackData.create(track_id="failure_001", name="Failure 1")),
                ("success_002", DummyTrackData.create(track_id="success_002", name="Success 2")),
            ]

            track_ids = [track_id for track_id, _ in tracks_data]

        with allure.step("Execute bulk update with expected failures"):
            success_count, failed_count = await retriever.update_album_tracks_bulk_async(
                track_ids=track_ids, year="2000"
            )

        with allure.step("Verify failure handling"):
            assert success_count == 2  # Two successful updates
            assert failed_count == 1  # One failed update

            # Verify all tracks were attempted
            assert mock_track_processor.update_track_async.call_count == 3

            allure.attach(str(success_count), "Successful Updates", allure.attachment_type.TEXT)
            allure.attach(str(failed_count), "Failed Updates", allure.attachment_type.TEXT)
            allure.attach("Partial success with graceful error handling", "Result Summary", allure.attachment_type.TEXT)
