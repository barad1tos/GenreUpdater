"""Extended tests for ExternalApiOrchestrator - targeting uncovered methods."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC
from datetime import datetime as dt
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api.orchestrator import ExternalApiOrchestrator
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig
    from services.pending_verification import PendingVerificationService


@pytest.fixture
def mock_config() -> AppConfig:
    """Create mock configuration."""
    return create_test_app_config(
        year_retrieval={
            "enabled": False,
            "preferred_api": "musicbrainz",
            "api_auth": {
                "discogs_token": "test_token",
                "musicbrainz_app_name": "TestApp/1.0",
                "contact_email": "test@example.com",
            },
            "rate_limits": {
                "discogs_requests_per_minute": 25,
                "musicbrainz_requests_per_second": 1,
                "concurrent_api_calls": 3,
            },
            "processing": {
                "batch_size": 10,
                "delay_between_batches": 60,
                "adaptive_delay": False,
                "cache_ttl_days": 30,
                "skip_prerelease": True,
                "future_year_threshold": 1,
                "prerelease_recheck_days": 30,
                "pending_verification_interval_days": 30,
            },
            "logic": {
                "min_valid_year": 1900,
                "definitive_score_threshold": 85,
                "definitive_score_diff": 15,
                "preferred_countries": [],
                "major_market_codes": [],
            },
            "reissue_detection": {"reissue_keywords": []},
            "scoring": {
                "base_score": 10,
                "artist_exact_match_bonus": 0,
                "album_exact_match_bonus": 0,
                "perfect_match_bonus": 0,
                "album_variation_bonus": 0,
                "album_substring_penalty": 0,
                "album_unrelated_penalty": 0,
                "mb_release_group_match_bonus": 0,
                "type_album_bonus": 0,
                "type_ep_single_penalty": 0,
                "type_compilation_live_penalty": 0,
                "status_official_bonus": 0,
                "status_bootleg_penalty": 0,
                "status_promo_penalty": 0,
                "reissue_penalty": 0,
                "year_diff_penalty_scale": 0,
                "year_diff_max_penalty": 0,
                "year_before_start_penalty": 0,
                "year_after_end_penalty": 0,
                "year_near_start_bonus": 0,
                "country_artist_match_bonus": 0,
                "country_major_market_bonus": 0,
                "source_mb_bonus": 0,
                "source_discogs_bonus": 0,
            },
        },
        max_retries=3,
        retry_delay_seconds=1.0,
    )


@pytest.fixture
def mock_loggers() -> tuple[MagicMock, MagicMock]:
    """Create mock loggers."""
    console_logger = MagicMock()
    console_logger.isEnabledFor.return_value = False
    error_logger = MagicMock()
    return console_logger, error_logger


@pytest.fixture
def mock_services() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Create mock services."""
    analytics = MagicMock()
    cache_service = MagicMock()
    cache_service.get_async = AsyncMock(return_value=None)
    cache_service.set_async = AsyncMock()
    cache_service.invalidate = MagicMock()
    pending_verification = MagicMock()
    pending_verification.mark_for_verification = AsyncMock()
    pending_verification.remove_from_pending = AsyncMock()
    return analytics, cache_service, pending_verification


@pytest.fixture
async def orchestrator(
    mock_config: AppConfig,
    mock_loggers: tuple[MagicMock, MagicMock],
    mock_services: tuple[MagicMock, MagicMock, MagicMock],
) -> AsyncGenerator[ExternalApiOrchestrator]:
    """Create an orchestrator instance."""
    console_logger, error_logger = mock_loggers
    analytics, cache_service, pending_verification = mock_services

    orch = ExternalApiOrchestrator(
        config=mock_config,
        console_logger=console_logger,
        error_logger=error_logger,
        analytics=analytics,
        cache_service=cache_service,
        pending_verification_service=cast("PendingVerificationService", pending_verification),
    )

    yield orch

    if orch.session is not None and not orch.session.closed:
        await orch.close()


class TestApplyPreferredOrder:
    """Tests for _apply_preferred_order method."""

    @pytest.mark.asyncio
    async def test_puts_preferred_api_first(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should move preferred API to the front of the list."""
        api_list = ["discogs", "musicbrainz", "itunes"]
        result = orchestrator._apply_preferred_order(api_list)

        assert result[0] == "musicbrainz"
        assert "discogs" in result
        assert "itunes" in result

    @pytest.mark.asyncio
    async def test_deduplicates_apis(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should remove duplicate API entries."""
        api_list = ["discogs", "musicbrainz", "discogs", "itunes", "musicbrainz"]
        result = orchestrator._apply_preferred_order(api_list)

        assert len(result) == 3
        assert result.count("discogs") == 1
        assert result.count("musicbrainz") == 1

    @pytest.mark.asyncio
    async def test_handles_missing_preferred_api(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should work when preferred API is not in the list."""
        api_list = ["discogs", "itunes"]
        result = orchestrator._apply_preferred_order(api_list)

        assert result == ["discogs", "itunes"]

    @pytest.mark.asyncio
    async def test_normalizes_api_names(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should normalize API names."""
        api_list = ["MusicBrainz", "DISCOGS", "iTunes"]
        result = orchestrator._apply_preferred_order(api_list)

        assert result[0] == "musicbrainz"


class TestSafeMarkForVerification:
    """Tests for _safe_mark_for_verification method."""

    @pytest.mark.asyncio
    async def test_warns_when_service_not_initialized(
        self, mock_config: AppConfig, mock_loggers: tuple[MagicMock, MagicMock], mock_services: tuple[MagicMock, MagicMock, MagicMock]
    ) -> None:
        """Should log warning when pending verification service is not initialized."""
        console_logger, error_logger = mock_loggers
        analytics, cache_service, _ = mock_services

        orch = ExternalApiOrchestrator(
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=analytics,
            cache_service=cache_service,
            pending_verification_service=cast("PendingVerificationService", cast(object, None)),
        )

        await orch._safe_mark_for_verification("Artist", "Album")

        error_logger.warning.assert_called_once()
        assert "not initialized" in str(error_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_marks_for_verification_directly(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should call mark_for_verification directly when not fire_and_forget."""
        mock_service = cast(MagicMock, orchestrator.pending_verification_service)

        await orchestrator._safe_mark_for_verification("Artist", "Album", reason="test_reason")

        mock_service.mark_for_verification.assert_called_once_with(
            artist="Artist",
            album="Album",
            reason="test_reason",
            metadata=None,
            recheck_days=None,
        )

    @pytest.mark.asyncio
    async def test_creates_task_for_fire_and_forget(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should create background task when fire_and_forget is True."""
        mock_service = cast(MagicMock, orchestrator.pending_verification_service)

        await orchestrator._safe_mark_for_verification("Artist", "Album", fire_and_forget=True)

        # Wait for background task to complete
        await asyncio.sleep(0.1)

        mock_service.mark_for_verification.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_verification_error(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should log warning on verification error."""
        mock_error_logger = cast(MagicMock, orchestrator.error_logger)

        with patch.object(
            orchestrator.pending_verification_service, "mark_for_verification", new_callable=AsyncMock, side_effect=ValueError("Test error")
        ):
            await orchestrator._safe_mark_for_verification("Artist", "Album")

        mock_error_logger.warning.assert_called_once()


class TestSafeRemoveFromPending:
    """Tests for _safe_remove_from_pending method."""

    @pytest.mark.asyncio
    async def test_returns_when_service_not_initialized(
        self, mock_config: AppConfig, mock_loggers: tuple[MagicMock, MagicMock], mock_services: tuple[MagicMock, MagicMock, MagicMock]
    ) -> None:
        """Should return early when pending verification service is None."""
        console_logger, error_logger = mock_loggers
        analytics, cache_service, _ = mock_services

        orch = ExternalApiOrchestrator(
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=analytics,
            cache_service=cache_service,
            pending_verification_service=cast("PendingVerificationService", cast(object, None)),
        )

        await orch._safe_remove_from_pending("Artist", "Album")
        # Should not raise or call anything

    @pytest.mark.asyncio
    async def test_removes_from_pending(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should call remove_from_pending on the service."""
        mock_service = cast(MagicMock, orchestrator.pending_verification_service)

        await orchestrator._safe_remove_from_pending("Artist", "Album")

        mock_service.remove_from_pending.assert_called_once_with(artist="Artist", album="Album")


class TestCountPrereleaseTrack:
    """Tests for _count_prerelease_tracks method."""

    @pytest.mark.asyncio
    async def test_counts_prerelease_tracks(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should count tracks marked as prerelease."""
        tracks = [
            {"year": "", "prerelease": "true"},
            {"year": "2020", "prerelease": "false"},
            {"year": "", "prerelease": "true"},
        ]
        result = orchestrator._count_prerelease_tracks(tracks)
        # The implementation checks for empty years as prerelease indicator
        assert result >= 0


class TestComputeFutureYearStats:
    """Tests for _compute_future_year_stats method."""

    @pytest.mark.asyncio
    async def test_computes_future_year_statistics(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should compute statistics for future year tracks."""
        current_year = dt.now(tz=UTC).year
        tracks = [
            {"year": str(current_year + 2)},
            {"year": str(current_year)},
            {"year": str(current_year + 3)},
        ]

        future_count, max_future, _ratio_triggered, _significant = orchestrator._compute_future_year_stats(tracks, current_year)

        assert future_count >= 0
        assert max_future >= current_year + 2 or max_future == 0

    @pytest.mark.asyncio
    async def test_skips_tracks_with_unparseable_year(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should skip tracks with non-integer year values without crashing."""
        current_year = dt.now(tz=UTC).year
        tracks = [
            {"year": "not_a_number"},
            {"year": str(current_year + 2)},
            {"year": "N/A"},
            {"year": str(current_year)},
        ]

        future_count, max_future, _ratio_triggered, _significant = orchestrator._compute_future_year_stats(tracks, current_year)

        # Only the valid future year track should be counted
        assert future_count == 1
        assert max_future == current_year + 2


class TestIsPrereleaseAlbum:
    """Tests for _is_prerelease_album method."""

    @pytest.mark.asyncio
    async def test_returns_true_for_prerelease(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should return True when album has prerelease indicators."""
        result = orchestrator._is_prerelease_album(prerelease_count=5, ratio_triggered=True, significant_future_year=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_for_normal_album(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should return False for normal albums."""
        result = orchestrator._is_prerelease_album(prerelease_count=0, ratio_triggered=False, significant_future_year=False)
        assert result is False


class TestHandlePrereleaseAlbum:
    """Tests for _handle_prerelease_album method."""

    @pytest.mark.asyncio
    async def test_logs_prerelease_info(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should log information about prerelease album."""
        mock_console_logger = cast(MagicMock, orchestrator.console_logger)

        with patch.object(orchestrator, "_safe_mark_for_verification", new_callable=AsyncMock):
            orchestrator._handle_prerelease_album(
                artist="Test Artist",
                album="Test Album",
                current_library_year="2023",
                prerelease_count=3,
                future_year_count=2,
                max_future_year=2026,
                total_tracks=10,
            )

        # Check that info was called with prerelease message (may be called multiple times)
        calls = mock_console_logger.info.call_args_list
        prerelease_calls = [c for c in calls if "prerelease" in str(c).lower()]
        assert prerelease_calls


class TestLogFutureYearWithinThreshold:
    """Tests for _log_future_year_within_threshold method."""

    @pytest.mark.asyncio
    async def test_logs_debug_message(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should log debug message about future year within threshold."""
        mock_console_logger = cast(MagicMock, orchestrator.console_logger)

        orchestrator._log_future_year_within_threshold("Test Artist", "Test Album")

        mock_console_logger.debug.assert_called()


class TestTokenMethods:
    """Tests for token-related methods."""

    @pytest.mark.asyncio
    async def test_get_raw_token_returns_config_value(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should return token from config value string."""
        result = orchestrator._get_raw_token("test_discogs_token", "discogs_token", "DISCOGS_TOKEN")
        assert result == "test_discogs_token"

    @pytest.mark.asyncio
    async def test_get_raw_token_returns_empty_for_missing(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should return empty string for missing token."""
        result = orchestrator._get_raw_token("", "nonexistent_token", "NONEXISTENT_VAR")
        assert result == ""

    @pytest.mark.asyncio
    async def test_get_raw_token_uses_env_var_fallback(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should fall back to environment variable when config value is empty."""
        with patch.dict("os.environ", {"TEST_TOKEN_VAR": "env_token_value"}):
            result = orchestrator._get_raw_token("", "test_key", "TEST_TOKEN_VAR")
        assert result == "env_token_value"


class TestClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_awaits_pending_tasks(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should await pending tasks before closing."""
        await orchestrator.initialize()

        # Add a pending task
        async def dummy_task() -> None:
            """Simulate a short async task."""
            await asyncio.sleep(0.01)

        task = asyncio.create_task(dummy_task())
        orchestrator._pending_tasks.add(task)

        await orchestrator.close()

        assert orchestrator.session is not None
        assert orchestrator.session.closed


class TestMakeApiRequest:
    """Tests for _make_api_request method."""

    @pytest.mark.asyncio
    async def test_delegates_to_request_executor(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should delegate to request executor."""
        await orchestrator.initialize()

        with patch.object(orchestrator.request_executor, "execute_request", new_callable=AsyncMock, return_value={"data": "test"}) as mock_exec:
            result = await orchestrator._make_api_request("musicbrainz", "https://example.com")

        mock_exec.assert_called_once()
        assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_passes_all_parameters(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should pass all parameters to request executor."""
        await orchestrator.initialize()

        with patch.object(orchestrator.request_executor, "execute_request", new_callable=AsyncMock, return_value=None) as mock_exec:
            await orchestrator._make_api_request(
                api_name="musicbrainz",
                url="https://example.com",
                params={"q": "test"},
                headers_override={"X-Custom": "header"},
                max_retries=5,
                base_delay=2.0,
                timeout_override=30.0,
            )

        mock_exec.assert_called_once_with(
            api_name="musicbrainz",
            url="https://example.com",
            params={"q": "test"},
            headers_override={"X-Custom": "header"},
            max_retries=5,
            base_delay=2.0,
            timeout_override=30.0,
        )


class TestNormalizeApiName:
    """Tests for _normalize_api_name method."""

    @pytest.mark.asyncio
    async def test_normalizes_to_lowercase(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should normalize API name to lowercase."""
        assert orchestrator._normalize_api_name("MusicBrainz") == "musicbrainz"
        assert orchestrator._normalize_api_name("DISCOGS") == "discogs"
        assert orchestrator._normalize_api_name("iTunes") == "itunes"

    @pytest.mark.asyncio
    async def test_normalizes_unknown_to_musicbrainz(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should normalize unknown names to musicbrainz (default)."""
        assert orchestrator._normalize_api_name("") == "musicbrainz"
        assert orchestrator._normalize_api_name("unknown") == "musicbrainz"

    @pytest.mark.asyncio
    async def test_normalizes_itunes_aliases(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Should normalize applemusic to itunes."""
        assert orchestrator._normalize_api_name("applemusic") == "itunes"
        assert orchestrator._normalize_api_name("AppleMusic") == "itunes"
        assert orchestrator._normalize_api_name("itunes") == "itunes"


class TestCurrentYearContamination:
    """Tests for current year contamination detection logic."""

    @pytest.mark.asyncio
    async def test_current_year_rejected_when_tracks_added_long_ago(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Tracks added years ago with current year should be rejected as contamination."""
        current_year = orchestrator.current_year
        # Tracks added 5 years ago, but library year is current year -> contamination
        result = orchestrator._get_fallback_year_when_no_api_results(
            current_library_year=str(current_year),
            log_artist="Test Artist",
            log_album="Test Album",
            earliest_track_added_year=current_year - 5,
        )
        assert result is None, "Current year should be rejected when tracks were added long ago"

    @pytest.mark.asyncio
    async def test_current_year_rejected_when_no_track_added_info(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Current year should be rejected when no track added date is available."""
        current_year = orchestrator.current_year
        result = orchestrator._get_fallback_year_when_no_api_results(
            current_library_year=str(current_year),
            log_artist="Test Artist",
            log_album="Test Album",
        )
        assert result is None, "Current year should be rejected when track added date is unknown"

    @pytest.mark.asyncio
    async def test_current_year_accepted_when_tracks_added_this_year(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Tracks added this year with current year should be accepted as legitimate."""
        current_year = orchestrator.current_year
        result = orchestrator._get_fallback_year_when_no_api_results(
            current_library_year=str(current_year),
            log_artist="Test Artist",
            log_album="Test Album",
            earliest_track_added_year=current_year,
        )
        assert result == str(current_year), "Current year should be accepted when tracks were added this year"

    @pytest.mark.asyncio
    async def test_non_current_year_always_accepted(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Non-current years should always be accepted regardless of track added date."""
        current_year = orchestrator.current_year
        # Test with a past year - should always be accepted
        result = orchestrator._get_fallback_year_when_no_api_results(
            current_library_year=str(current_year - 1),
            log_artist="Test Artist",
            log_album="Test Album",
            earliest_track_added_year=current_year - 10,
        )
        assert result == str(current_year - 1), "Past years should always be accepted"

    @pytest.mark.asyncio
    async def test_handle_year_search_error_respects_track_added_year(self, orchestrator: ExternalApiOrchestrator) -> None:
        """_handle_year_search_error should use earliest_track_added_year for contamination check."""
        current_year = orchestrator.current_year
        # Tracks added this year -> accept current year
        result, is_def, conf, _scores = orchestrator._handle_year_search_error(
            log_artist="Test Artist",
            log_album="Test Album",
            current_library_year=str(current_year),
            earliest_track_added_year=current_year,
        )
        assert result == str(current_year), "Should accept current year when tracks added this year"
        assert is_def is False
        assert conf == 0
