"""Enhanced TrackProcessor tests with Allure reporting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import allure
import pytest
from src.domain.tracks.track_processor import TrackProcessor
from src.shared.data.validators import SecurityValidator

from tests.mocks.csv_mock import MockAnalytics, MockLogger
from tests.mocks.track_data import DummyTrackData

if TYPE_CHECKING:
    from src.shared.data.models import TrackDict
    from src.shared.data.protocols import AppleScriptClientProtocol, CacheServiceProtocol


@allure.epic("Music Genre Updater")
@allure.feature("Track Processing")
class TestTrackProcessorAllure:
    """Enhanced tests for TrackProcessor with Allure reporting."""

    def create_processor(
        self,
        ap_client: AppleScriptClientProtocol | None = None,
        cache_service: CacheServiceProtocol | None = None,
        config: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> TrackProcessor:
        """Create a TrackProcessor instance for testing."""
        if ap_client is None:
            ap_client = MagicMock()
            ap_client.fetch_tracks_async = AsyncMock(return_value=[])
            ap_client.update_track_async = AsyncMock(return_value="Success")

        if cache_service is None:
            cache_service = MagicMock()
            cache_service.get_async = AsyncMock(return_value=None)
            cache_service.set_async = AsyncMock()

        console_logger = MockLogger()  # type: ignore[assignment]
        error_logger = MockLogger()  # type: ignore[assignment]
        analytics = MockAnalytics()  # type: ignore[assignment]

        test_config = config or {"apple_script": {"timeout": 30}, "development": {"test_artists": ["Test Artist"]}}

        return TrackProcessor(
            ap_client=ap_client,
            cache_service=cache_service,
            console_logger=console_logger,
            error_logger=error_logger,
            config=test_config,
            analytics=analytics,
            dry_run=dry_run,
        )

    @allure.story("Initialization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should initialize TrackProcessor with all dependencies")
    @allure.description("Test that TrackProcessor initializes correctly with required dependencies")
    def test_processor_initialization_comprehensive(self) -> None:
        """Test comprehensive TrackProcessor initialization."""
        with allure.step("Setup mock dependencies"):
            mock_ap_client = MagicMock()
            mock_cache_service = MagicMock()
            mock_security_validator = SecurityValidator(MockLogger())  # type: ignore[arg-type]

        with allure.step("Initialize processor with custom security validator"):
            processor = TrackProcessor(
                ap_client=mock_ap_client,
                cache_service=mock_cache_service,
                console_logger=MockLogger(),
                error_logger=MockLogger(),
                config={"apple_script": {"timeout": 30}},
                analytics=MockAnalytics(),
                dry_run=True,
                security_validator=mock_security_validator,
            )

        with allure.step("Verify initialization"):
            assert processor.ap_client is mock_ap_client
            assert processor.cache_service is mock_cache_service
            assert processor.dry_run is True
            assert processor.security_validator is mock_security_validator
            assert isinstance(processor._dry_run_actions, list)  # noqa: SLF001

            allure.attach("TrackProcessor initialized successfully", "Initialization Result", allure.attachment_type.TEXT)

    @allure.story("Dry Run Context")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should set and use dry run context correctly")
    @allure.description("Test dry run context configuration for test mode filtering")
    def test_set_dry_run_context_detailed(self) -> None:
        """Test setting dry run context with detailed validation."""
        processor = self.create_processor()

        with allure.step("Set dry run context"):
            test_mode = "test"
            test_artists = {"Artist1", "Artist2", "Artist3"}
            processor.set_dry_run_context(test_mode, test_artists)

        with allure.step("Verify context is set correctly"):
            assert processor.dry_run_mode == test_mode
            assert processor.dry_run_test_artists == test_artists

            allure.attach(test_mode, "Dry Run Mode", allure.attachment_type.TEXT)
            allure.attach(str(test_artists), "Test Artists", allure.attachment_type.TEXT)

    @allure.story("Security Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should validate track security properly")
    @allure.description("Test security validation of track data to prevent malicious content")
    def test_validate_tracks_security_comprehensive(self) -> None:
        """Test comprehensive track security validation."""
        processor = self.create_processor()

        with allure.step("Create test tracks with various security scenarios"):
            safe_track = DummyTrackData.create(track_id="safe_001", name="Safe Track", artist="Safe Artist", genre="Rock")

            # Create potentially unsafe track (but within bounds for testing)
            edge_case_track = DummyTrackData.create(
                track_id="edge_001", name="Track with Special Characters: !@#$%", artist="Artist & Co.", genre="Rock/Pop"
            )

            test_tracks = [safe_track, edge_case_track]

            allure.attach(f"Input tracks count: {len(test_tracks)}", "Input Data", allure.attachment_type.TEXT)

        with allure.step("Execute security validation"):
            validated_tracks = processor._validate_tracks_security(test_tracks)  # noqa: SLF001

        with allure.step("Verify validation results"):
            assert isinstance(validated_tracks, list)
            assert len(validated_tracks) <= len(test_tracks)  # Some might be filtered out

            # All returned tracks should be valid
            for track in validated_tracks:
                assert hasattr(track, "id")
                assert hasattr(track, "name")
                assert hasattr(track, "artist")

            allure.attach(f"Validated tracks count: {len(validated_tracks)}", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("AppleScript Timeout")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should calculate AppleScript timeout correctly")
    @allure.description("Test timeout calculation for single artist vs. full library operations")
    @pytest.mark.parametrize(
        ("is_single_artist", "expected_multiplier"),
        [
            (True, 1),  # Single artist gets base timeout
            (False, 3),  # Full library gets extended timeout
        ],
    )
    def test_applescript_timeout_calculation(self, is_single_artist: bool, expected_multiplier: int) -> None:
        """Test AppleScript timeout calculation for different scenarios."""
        base_timeout = 30
        config = {"apple_script": {"timeout": base_timeout}}
        processor = self.create_processor(config=config)

        with allure.step(f"Calculate timeout for single_artist={is_single_artist}"):
            timeout = processor._get_applescript_timeout(is_single_artist)  # noqa: SLF001

        with allure.step("Verify timeout calculation"):
            expected_timeout = base_timeout * expected_multiplier
            assert timeout == expected_timeout

            allure.attach(str(is_single_artist), "Is Single Artist", allure.attachment_type.TEXT)
            allure.attach(str(timeout), "Calculated Timeout", allure.attachment_type.TEXT)
            allure.attach(str(expected_timeout), "Expected Timeout", allure.attachment_type.TEXT)

    @allure.story("Track Fetching")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should fetch tracks from AppleScript successfully")
    @allure.description("Test successful track fetching from Music.app via AppleScript")
    @pytest.mark.asyncio
    async def test_fetch_tracks_from_applescript_success(self) -> None:
        """Test successful track fetching from AppleScript."""
        with allure.step("Setup mock AppleScript client"):
            mock_ap_client = MagicMock()
            mock_tracks_data = [
                DummyTrackData.create(track_id="1", name="Track 1", artist="Artist 1"),
                DummyTrackData.create(track_id="2", name="Track 2", artist="Artist 2"),
            ]
            mock_ap_client.fetch_tracks_async = AsyncMock(return_value=mock_tracks_data)

        with allure.step("Create processor with mock client"):
            processor = self.create_processor(ap_client=mock_ap_client)

        with allure.step("Execute track fetching"):
            result = await processor._fetch_tracks_from_applescript("Test Artist", True)  # noqa: SLF001

        with allure.step("Verify fetching results"):
            assert isinstance(result, list)
            assert len(result) == 2

            # Verify tracks are properly structured
            for track in result:
                assert hasattr(track, "id")
                assert hasattr(track, "name")
                assert hasattr(track, "artist")

            allure.attach(f"Fetched {len(result)} tracks", "Fetch Result", allure.attachment_type.TEXT)
            allure.attach(str([track.name for track in result]), "Track Names", allure.attachment_type.TEXT)

    @allure.story("Track Updates")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should update track properties successfully")
    @allure.description("Test successful track property updates via AppleScript")
    @pytest.mark.asyncio
    async def test_update_track_async_success(self) -> None:
        """Test successful track property update."""
        with allure.step("Setup mock AppleScript client"):
            mock_ap_client = MagicMock()
            mock_ap_client.update_track_async = AsyncMock(return_value="Success: Genre updated")

        with allure.step("Create processor with mock client"):
            processor = self.create_processor(ap_client=mock_ap_client)

        with allure.step("Execute track update"):
            success = await processor.update_track_async(
                track_id="test_001", new_genre="Jazz", original_artist="Test Artist", original_album="Test Album", original_track="Test Track"
            )

        with allure.step("Verify update success"):
            assert success is True

            # Verify AppleScript client was called correctly
            mock_ap_client.update_track_async.assert_called_once()
            call_args = mock_ap_client.update_track_async.call_args

            allure.attach("True", "Update Success", allure.attachment_type.TEXT)
            allure.attach(str(call_args), "AppleScript Call Args", allure.attachment_type.TEXT)

    @allure.story("Dry Run Mode")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle dry run updates correctly")
    @allure.description("Test dry run mode behavior without actual track modifications")
    @pytest.mark.asyncio
    async def test_dry_run_update_behavior(self) -> None:
        """Test dry run update behavior with comprehensive validation."""
        with allure.step("Create processor in dry run mode"):
            processor = self.create_processor(dry_run=True)

        with allure.step("Execute dry run update"):
            success = await processor.update_track_async(
                track_id="dry_run_001",
                new_genre="Electronic",
                original_artist="Dry Run Artist",
                original_album="Dry Run Album",
                original_track="Dry Run Track",
            )

        with allure.step("Verify dry run behavior"):
            assert success is True  # Dry run should always succeed

            # Check that dry run action was recorded
            dry_run_actions = processor.get_dry_run_actions()
            assert len(dry_run_actions) > 0

            latest_action = dry_run_actions[-1]
            assert latest_action["track_id"] == "dry_run_001"
            assert latest_action["property_name"] == "genre"
            assert latest_action["new_value"] == "Electronic"

            allure.attach(str(len(dry_run_actions)), "Dry Run Actions Count", allure.attachment_type.TEXT)
            allure.attach(str(latest_action), "Latest Dry Run Action", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle AppleScript errors gracefully")
    @allure.description("Test error handling when AppleScript operations fail")
    @pytest.mark.asyncio
    async def test_applescript_error_handling(self) -> None:
        """Test error handling for AppleScript failures."""
        with allure.step("Setup failing AppleScript client"):
            mock_ap_client = MagicMock()
            mock_ap_client.update_track_async = AsyncMock(side_effect=Exception("AppleScript Error"))

        with allure.step("Create processor with failing client"):
            processor = self.create_processor(ap_client=mock_ap_client)

        with allure.step("Execute update that should fail"):
            success = await processor.update_track_async(
                track_id="error_001", new_genre="Rock", original_artist="Error Artist", original_album="Error Album", original_track="Error Track"
            )

        with allure.step("Verify error handling"):
            assert success is False  # Should return False on error

            # Verify error was logged
            error_messages = processor.error_logger.error_messages  # type: ignore[attr-defined]
            assert len(error_messages) > 0
            assert any("Error updating track" in msg for msg in error_messages)

            allure.attach("False", "Update Success", allure.attachment_type.TEXT)
            allure.attach(str(error_messages), "Error Messages", allure.attachment_type.TEXT)

    @allure.story("Batch Processing")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should process tracks in batches efficiently")
    @allure.description("Test batch processing functionality for large track collections")
    @pytest.mark.asyncio
    async def test_fetch_tracks_in_batches(self) -> None:
        """Test batch processing of track fetching."""
        with allure.step("Setup mock AppleScript client for batch processing"):
            mock_ap_client = MagicMock()

            # Mock track summary data
            mock_summaries = [
                f"Track {i}|Artist {i}|Album {i}|{i}"
                for i in range(15)  # 15 tracks to test batching
            ]
            mock_ap_client.fetch_track_summaries_async = AsyncMock(return_value=mock_summaries)

            # Mock individual track fetching
            def mock_fetch_by_ids(track_ids: list[str]) -> list[TrackDict]:
                return [DummyTrackData.create(track_id=tid, name=f"Track {tid}", artist=f"Artist {tid}") for tid in track_ids]

            mock_ap_client.fetch_tracks_by_ids_async = AsyncMock(side_effect=mock_fetch_by_ids)

        with allure.step("Create processor with batch configuration"):
            processor = self.create_processor(ap_client=mock_ap_client)

        with allure.step("Execute batch processing"):
            tracks = await processor.fetch_tracks_in_batches(batch_size=5)

        with allure.step("Verify batch processing results"):
            assert isinstance(tracks, list)
            assert len(tracks) > 0

            # Should have processed tracks in batches
            # Verify AppleScript client methods were called
            mock_ap_client.fetch_track_summaries_async.assert_called()

            allure.attach(f"Processed {len(tracks)} tracks", "Batch Processing Result", allure.attachment_type.TEXT)
            allure.attach("5", "Batch Size", allure.attachment_type.TEXT)
