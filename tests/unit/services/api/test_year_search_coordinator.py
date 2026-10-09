"""Tests for YearSearchCoordinator - coordinating API calls for release years."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.protocols import YearLookupUnavailableError
from core.models.script_detection import ScriptType
from services.api.request_executor import ApiRequestError
from services.api.year_scoring import ArtistContext
from services.api.year_search_coordinator import ProviderTally, YearSearchCoordinator
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


@pytest.fixture
def mock_musicbrainz_client() -> AsyncMock:
    """Create mock MusicBrainz client."""
    client = AsyncMock()
    client.get_scored_releases = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_discogs_client() -> AsyncMock:
    """Create mock Discogs client."""
    client = AsyncMock()
    client.get_scored_releases = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_applemusic_client() -> AsyncMock:
    """Create mock Apple Music client."""
    client = AsyncMock()
    client.get_scored_releases = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_release_scorer() -> MagicMock:
    """Create mock release scorer."""
    return MagicMock()


@pytest.fixture
def default_config() -> AppConfig:
    """Create default configuration."""
    return create_test_app_config(
        year_retrieval={
            **create_test_app_config().year_retrieval.model_dump(),
            "script_api_priorities": {
                "default": {
                    "primary": ["musicbrainz"],
                    "fallback": ["discogs"],
                },
                "cyrillic": {
                    "primary": ["discogs", "musicbrainz"],
                    "fallback": ["itunes"],
                },
            },
        },
    )


@pytest.fixture
def coordinator(
    *,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    default_config: AppConfig,
    mock_musicbrainz_client: AsyncMock,
    mock_discogs_client: AsyncMock,
    mock_applemusic_client: AsyncMock,
    mock_release_scorer: MagicMock,
) -> YearSearchCoordinator:
    """Create a YearSearchCoordinator instance."""
    return YearSearchCoordinator(
        console_logger=console_logger,
        error_logger=error_logger,
        config=default_config,
        preferred_api="musicbrainz",
        musicbrainz_client=mock_musicbrainz_client,
        discogs_client=mock_discogs_client,
        applemusic_client=mock_applemusic_client,
        release_scorer=mock_release_scorer,
    )


@pytest.fixture
def coordinator_factory(
    *,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    default_config: AppConfig,
    mock_musicbrainz_client: AsyncMock,
    mock_discogs_client: AsyncMock,
    mock_applemusic_client: AsyncMock,
    mock_release_scorer: MagicMock,
) -> Callable[..., YearSearchCoordinator]:
    """Build a YearSearchCoordinator over the shared mocks, with Discogs enabled or not."""

    def build(*, discogs_enabled: bool) -> YearSearchCoordinator:
        """Build the coordinator."""
        return YearSearchCoordinator(
            console_logger=console_logger,
            error_logger=error_logger,
            config=default_config,
            preferred_api="musicbrainz",
            musicbrainz_client=mock_musicbrainz_client,
            discogs_client=mock_discogs_client,
            applemusic_client=mock_applemusic_client,
            release_scorer=mock_release_scorer,
            discogs_enabled=discogs_enabled,
        )

    return build


class TestInitialization:
    """Tests for YearSearchCoordinator initialization."""

    def test_init_stores_all_parameters(
        self,
        *,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        default_config: AppConfig,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
        mock_release_scorer: MagicMock,
    ) -> None:
        """Test initialization stores all parameters."""
        coordinator = YearSearchCoordinator(
            console_logger=console_logger,
            error_logger=error_logger,
            config=default_config,
            preferred_api="discogs",
            musicbrainz_client=mock_musicbrainz_client,
            discogs_client=mock_discogs_client,
            applemusic_client=mock_applemusic_client,
            release_scorer=mock_release_scorer,
        )

        assert coordinator.preferred_api == "discogs"


class TestNormalizeApiName:
    """Tests for _normalize_api_name static method."""

    def test_normalize_string(self) -> None:
        """Test normalizing a string API name."""
        assert YearSearchCoordinator._normalize_api_name("MusicBrainz") == "musicbrainz"

    def test_normalize_with_whitespace(self) -> None:
        """Test normalizing with whitespace."""
        assert YearSearchCoordinator._normalize_api_name("  Discogs  ") == "discogs"

    def test_normalize_non_string(self) -> None:
        """Test normalizing non-string value."""
        assert YearSearchCoordinator._normalize_api_name(123) == "123"

    def test_normalize_none(self) -> None:
        """Test normalizing None."""
        assert YearSearchCoordinator._normalize_api_name(None) == "unknown"


class TestApplyPreferredOrder:
    """Tests for _apply_preferred_order method."""

    def test_moves_preferred_to_front(self, coordinator: YearSearchCoordinator) -> None:
        """Test preferred API is moved to front."""
        api_list = ["discogs", "musicbrainz", "itunes"]

        result = coordinator._apply_preferred_order(api_list)

        assert result[0] == "musicbrainz"
        assert "discogs" in result
        assert "itunes" in result

    def test_no_change_when_not_in_list(self, coordinator: YearSearchCoordinator) -> None:
        """Test no change when preferred API not in list."""
        self._assert_preferred_order_unchanged(coordinator, ["discogs", "itunes"])

    def test_no_preferred_api(
        self,
        *,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        default_config: AppConfig,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
        mock_release_scorer: MagicMock,
    ) -> None:
        """Test when no preferred API is set."""
        test_coordinator = YearSearchCoordinator(
            console_logger=console_logger,
            error_logger=error_logger,
            config=default_config,
            preferred_api="",
            musicbrainz_client=mock_musicbrainz_client,
            discogs_client=mock_discogs_client,
            applemusic_client=mock_applemusic_client,
            release_scorer=mock_release_scorer,
        )
        self._assert_preferred_order_unchanged(test_coordinator, ["discogs", "musicbrainz"])

    @staticmethod
    def _assert_preferred_order_unchanged(
        test_coordinator: YearSearchCoordinator,
        api_list: list[str],
    ) -> None:
        """Assert that preferred order returns the list unchanged."""
        result = test_coordinator._apply_preferred_order(api_list)
        assert result == api_list


class TestGetApiClient:
    """Tests for _get_api_client method."""

    def test_get_musicbrainz_client(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
    ) -> None:
        """Test getting MusicBrainz client."""
        client = coordinator._get_api_client("musicbrainz")
        assert client is mock_musicbrainz_client

    def test_get_discogs_client(
        self,
        coordinator: YearSearchCoordinator,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """Test getting Discogs client."""
        client = coordinator._get_api_client("discogs")
        assert client is mock_discogs_client

    def test_get_itunes_client(
        self,
        coordinator: YearSearchCoordinator,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """Test getting iTunes/AppleMusic client."""
        client = coordinator._get_api_client("itunes")
        assert client is mock_applemusic_client

    def test_get_applemusic_alias(
        self,
        coordinator: YearSearchCoordinator,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """Test getting AppleMusic client via alias."""
        client = coordinator._get_api_client("applemusic")
        assert client is mock_applemusic_client

    def test_get_unknown_client(self, coordinator: YearSearchCoordinator) -> None:
        """Test getting unknown client returns None."""
        client = coordinator._get_api_client("unknown")
        assert client is None


class TestGetScriptApiPriorities:
    """Tests for _get_script_api_priorities method."""

    def test_get_cyrillic_priorities(self, coordinator: YearSearchCoordinator) -> None:
        """Test getting Cyrillic script priorities."""
        priorities = coordinator._get_script_api_priorities(ScriptType.CYRILLIC)

        assert "discogs" in priorities["primary"]
        assert "musicbrainz" in priorities["primary"]

    def test_get_default_priorities_for_unknown_script(self, coordinator: YearSearchCoordinator) -> None:
        """Test getting default priorities for unknown script."""
        priorities = coordinator._get_script_api_priorities(ScriptType.LATIN)

        # Should fall back to default
        assert "musicbrainz" in priorities["primary"]


class TestProcessApiTaskResults:
    """Tests for _process_api_task_results method."""

    def test_processes_successful_results(self, coordinator: YearSearchCoordinator) -> None:
        """Test processing successful results."""
        results: list[Any] = [
            [{"title": "Album1", "year": "2020", "score": 85}],
            [{"title": "Album2", "year": "2021", "score": 90}],
        ]
        self._assert_processed_results_count(coordinator, results, 2)

    def test_handles_exceptions(self, coordinator: YearSearchCoordinator) -> None:
        """Test handling exceptions in results."""
        results: list[Any] = [
            [{"title": "Album1", "year": "2020", "score": 85}],
            ValueError("API error"),
        ]
        tally = self._assert_processed_results_count(coordinator, results, 1)
        assert tally.failed

    def test_handles_empty_results(self, coordinator: YearSearchCoordinator) -> None:
        """Test handling empty results."""
        results: list[Any] = [[], []]
        tally = self._assert_processed_results_count(coordinator, results, 0)
        assert not tally.failed

    @staticmethod
    def _assert_processed_results_count(
        coordinator: YearSearchCoordinator,
        results: list[Any],
        expected_count: int,
    ) -> ProviderTally:
        """Assert that processing results yields expected count, and return the search's tally."""
        api_order = ["musicbrainz", "discogs"]
        tally = ProviderTally()
        processed = coordinator._process_api_task_results(results, api_order, "Artist", "Album", tally=tally)
        assert len(processed) == expected_count
        return tally


class TestFetchAllApiResults:
    """Tests for fetch_all_api_results method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_results(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """Test returns empty list when no APIs have results."""
        mock_musicbrainz_client.get_scored_releases.return_value = []
        mock_discogs_client.get_scored_releases.return_value = []

        results = await coordinator.fetch_all_api_results("pink floyd", "dark side", ArtistContext(), "Pink Floyd", "Dark Side")

        assert results == []

    @pytest.mark.asyncio
    async def test_combines_results_from_multiple_apis(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """Test combines results from multiple APIs."""
        mock_musicbrainz_client.get_scored_releases.return_value = [{"title": "Album", "year": "2020", "score": 85}]
        mock_discogs_client.get_scored_releases.return_value = [{"title": "Album", "year": "2020", "score": 90}]

        results = await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album")

        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_every_provider_scores_with_the_search_context(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """Each provider receives the lookup's own artist context, so its releases score against that artist."""
        artist_context = ArtistContext(region="GB", period={"start_year": 1965, "end_year": 2014})
        for client in (mock_musicbrainz_client, mock_discogs_client, mock_applemusic_client):
            client.get_scored_releases.return_value = [{"title": "Album", "year": "1973", "score": 85}]

        await coordinator.fetch_all_api_results("pink floyd", "dark side", artist_context, "Pink Floyd", "Dark Side")

        for client in (mock_musicbrainz_client, mock_discogs_client, mock_applemusic_client):
            assert client.get_scored_releases.await_args.args[2] is artist_context

    @pytest.mark.asyncio
    async def test_script_search_scores_with_the_search_context(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
    ) -> None:
        """The non-Latin search hands the lookup's context to the provider it tries first."""
        artist_context = ArtistContext(region="JP", period={"start_year": 1990, "end_year": None})
        mock_musicbrainz_client.get_scored_releases.return_value = [{"title": "アルバム", "year": "1995", "score": 85}]

        await coordinator.fetch_all_api_results("ドリカム", "アルバム", artist_context, "ドリカム", "アルバム")

        assert mock_musicbrainz_client.get_scored_releases.await_args.args[2] is artist_context


class TestTrySingleApi:
    """Tests for _try_single_api method."""

    @pytest.mark.asyncio
    async def test_returns_results_on_success(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
    ) -> None:
        """Test returns results on successful API call."""
        mock_musicbrainz_client.get_scored_releases.return_value = [{"title": "Album", "year": "2020", "score": 85}]

        results = await coordinator._try_single_api(
            "musicbrainz",
            artist_norm="artist",
            album_norm="album",
            artist_context=ArtistContext(),
            script_type=ScriptType.LATIN,
            is_fallback=False,
            tally=ProviderTally(),
        )

        assert results is not None
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_unknown_api(self, coordinator: YearSearchCoordinator) -> None:
        """Test returns None for unknown API."""
        results = await coordinator._try_single_api(
            "unknown",
            artist_norm="artist",
            album_norm="album",
            artist_context=ArtistContext(),
            script_type=ScriptType.LATIN,
            is_fallback=False,
            tally=ProviderTally(),
        )

        assert results is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_results(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
    ) -> None:
        """Test returns None when API returns empty results."""
        mock_musicbrainz_client.get_scored_releases.return_value = []

        results = await coordinator._try_single_api(
            "musicbrainz",
            artist_norm="artist",
            album_norm="album",
            artist_context=ArtistContext(),
            script_type=ScriptType.LATIN,
            is_fallback=False,
            tally=ProviderTally(),
        )

        assert results is None

    @pytest.mark.asyncio
    async def test_handles_api_exception(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
    ) -> None:
        """A provider exception yields no results and marks the search as having a failed provider."""
        mock_musicbrainz_client.get_scored_releases.side_effect = ValueError("API error")
        tally = ProviderTally()

        results = await coordinator._try_single_api(
            "musicbrainz",
            artist_norm="artist",
            album_norm="album",
            artist_context=ArtistContext(),
            script_type=ScriptType.LATIN,
            is_fallback=False,
            tally=tally,
        )

        assert results is None
        assert tally.failed

    @pytest.mark.asyncio
    async def test_logs_api_exception_with_traceback(
        self,
        *,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        error_logger: logging.Logger,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A provider that raises in the script search reaches the error log with its traceback, with API debugging off."""
        api_error = ValueError("API error")
        mock_musicbrainz_client.get_scored_releases.side_effect = api_error

        with caplog.at_level(logging.WARNING, logger=error_logger.name), patch("services.api.year_search_coordinator.debug") as mock_debug:
            mock_debug.api = False
            await coordinator._try_single_api(
                "musicbrainz",
                artist_norm="artist",
                album_norm="album",
                artist_context=ArtistContext(),
                script_type=ScriptType.CYRILLIC,
                is_fallback=False,
                tally=ProviderTally(),
            )

        error_records = [record for record in caplog.records if record.name == error_logger.name]
        assert len(error_records) == 1
        logged_exception = error_records[0].exc_info
        assert logged_exception is not None
        assert logged_exception[1] is api_error
        assert logged_exception[2] is not None  # the traceback itself, not only the exception


class TestTryApiList:
    """Tests for _try_api_list method."""

    @pytest.mark.asyncio
    async def test_returns_first_successful_result(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """Test returns first successful result."""
        mock_musicbrainz_client.get_scored_releases.return_value = []
        mock_discogs_client.get_scored_releases.return_value = [{"title": "Album", "year": "2020", "score": 85}]

        results = await coordinator._try_api_list(
            ["musicbrainz", "discogs"],
            artist_norm="artist",
            album_norm="album",
            artist_context=ArtistContext(),
            script_type=ScriptType.LATIN,
            is_fallback=False,
            tally=ProviderTally(),
        )

        assert results is not None
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_all_fail(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """Test returns None when all APIs fail."""
        mock_musicbrainz_client.get_scored_releases.return_value = []
        mock_discogs_client.get_scored_releases.return_value = []

        results = await coordinator._try_api_list(
            ["musicbrainz", "discogs"],
            artist_norm="artist",
            album_norm="album",
            artist_context=ArtistContext(),
            script_type=ScriptType.LATIN,
            is_fallback=False,
            tally=ProviderTally(),
        )

        assert results is None


class TestLogMethods:
    """Tests for logging methods."""

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(ValueError("Test error"), id="ValueError"),
            pytest.param(asyncio.CancelledError(), id="CancelledError"),
        ],
    )
    def test_log_api_error(
        self,
        coordinator: YearSearchCoordinator,
        error_logger: logging.Logger,
        caplog: pytest.LogCaptureFixture,
        error: BaseException,
    ) -> None:
        """An API error is logged with its type, so a cancelled search, whose message is empty, still says what happened."""
        with caplog.at_level(logging.WARNING, logger=error_logger.name):
            coordinator._log_api_error("musicbrainz", "Artist", "Album", error)

        messages = [record.getMessage() for record in caplog.records if record.name == error_logger.name]
        assert len(messages) == 1
        assert type(error).__name__ in messages[0]

    def test_log_empty_api_result(self, coordinator: YearSearchCoordinator) -> None:
        """Test _log_empty_api_result doesn't raise."""
        # Should not raise
        coordinator._log_empty_api_result("musicbrainz", "Artist", "Album")

    def test_log_api_summary(self, coordinator: YearSearchCoordinator) -> None:
        """Test _log_api_summary doesn't raise."""
        # Should not raise
        coordinator._log_api_summary("Artist", "Album", 5)


class TestExecuteStandardApiSearch:
    """Tests for _execute_standard_api_search method."""

    @pytest.mark.asyncio
    async def test_executes_all_apis_concurrently(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """Test executes all APIs."""
        mock_musicbrainz_client.get_scored_releases.return_value = [{"title": "Album", "year": "2020", "score": 85}]
        mock_discogs_client.get_scored_releases.return_value = []
        mock_applemusic_client.get_scored_releases.return_value = []

        results = await coordinator._execute_standard_api_search("artist", "album", ArtistContext(), "Artist", "Album", tally=ProviderTally())

        assert len(results) >= 1
        mock_musicbrainz_client.get_scored_releases.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_unavailable_api_client(
        self,
        *,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """Test that unavailable API clients are filtered out of concurrent search.

        When _get_api_client returns None for one provider (discogs),
        only the remaining providers should have tasks created.
        """
        mock_musicbrainz_client.get_scored_releases.return_value = [{"title": "Album", "year": "2020", "score": 85}]
        mock_applemusic_client.get_scored_releases.return_value = []

        # Patch _get_api_client to return None for discogs
        original_get = coordinator._get_api_client

        def selective_get(api_name: str) -> Any:
            """Return no Discogs client, and the real client for every other API."""
            return None if api_name == "discogs" else original_get(api_name)

        with patch.object(coordinator, "_get_api_client", side_effect=selective_get):
            results = await coordinator._execute_standard_api_search("artist", "album", ArtistContext(), "Artist", "Album", tally=ProviderTally())

        assert len(results) >= 1
        mock_musicbrainz_client.get_scored_releases.assert_called_once()
        mock_discogs_client.get_scored_releases.assert_not_called()

    @pytest.mark.asyncio
    async def test_logs_failed_provider_with_traceback(
        self,
        *,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        error_logger: logging.Logger,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A provider that raises in the concurrent search reaches the error log with its traceback."""
        api_error = ValueError("API error")
        mock_musicbrainz_client.get_scored_releases.side_effect = api_error

        with caplog.at_level(logging.WARNING, logger=error_logger.name):
            await coordinator._execute_standard_api_search("artist", "album", ArtistContext(), "Artist", "Album", tally=ProviderTally())

        error_records = [record for record in caplog.records if record.name == error_logger.name]
        assert len(error_records) == 1
        logged_exception = error_records[0].exc_info
        assert logged_exception is not None
        assert logged_exception[1] is api_error
        assert logged_exception[2] is not None  # the traceback itself, not only the exception


class TestScriptOptimizedSearch:
    """Tests for script-optimized search."""

    @pytest.mark.asyncio
    async def test_uses_script_optimized_for_cyrillic(
        self,
        coordinator: YearSearchCoordinator,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """Test uses script-optimized search for Cyrillic."""
        mock_discogs_client.get_scored_releases.return_value = [{"title": "Альбом", "year": "2020", "score": 85}]

        results = await coordinator.fetch_all_api_results(
            "московский исполнитель",
            "альбом",
            ArtistContext(),
            "Московский Исполнитель",  # Cyrillic artist
            "Альбом",
        )

        # Should have results from script-optimized search
        assert len(results) >= 1


class TestDebugApiLogging:
    """Tests for debug.api-guarded log lines in YearSearchCoordinator."""

    @pytest.fixture
    def debug_coordinator(
        self,
        default_config,
        mock_musicbrainz_client,
        mock_discogs_client,
        mock_applemusic_client,
        mock_release_scorer,
    ) -> tuple[YearSearchCoordinator, MagicMock, MagicMock]:
        """Create a YearSearchCoordinator with mocked loggers for debug tests."""
        mock_console = MagicMock(spec=logging.Logger)
        mock_error = MagicMock(spec=logging.Logger)
        coordinator = YearSearchCoordinator(
            console_logger=mock_console,
            error_logger=mock_error,
            config=default_config,
            preferred_api="musicbrainz",
            musicbrainz_client=mock_musicbrainz_client,
            discogs_client=mock_discogs_client,
            applemusic_client=mock_applemusic_client,
            release_scorer=mock_release_scorer,
        )
        return coordinator, mock_console, mock_error

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_script_optimized_search_logs_script_detected(
        self,
        mock_discogs_client,
        debug_coordinator,
    ) -> None:
        coordinator, mock_console, _ = debug_coordinator
        mock_discogs_client.get_scored_releases.return_value = [{"title": "A", "year": "2020", "score": 90}]

        with patch("services.api.year_search_coordinator.debug") as mock_debug:
            mock_debug.api = True
            await coordinator._try_script_optimized_search(ScriptType.CYRILLIC, "artist", "album", ArtistContext(), tally=ProviderTally())

        mock_console.info.assert_any_call("%s detected - trying script-optimized search", "cyrillic")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_script_optimized_search_logs_primary_failed_fallback(
        self,
        mock_musicbrainz_client,
        mock_discogs_client,
        mock_applemusic_client,
        debug_coordinator,
    ) -> None:
        coordinator, mock_console, _ = debug_coordinator
        # All APIs return empty to trigger fallback log
        mock_musicbrainz_client.get_scored_releases.return_value = []
        mock_discogs_client.get_scored_releases.return_value = []
        mock_applemusic_client.get_scored_releases.return_value = []

        with patch("services.api.year_search_coordinator.debug") as mock_debug:
            mock_debug.api = True
            await coordinator._try_script_optimized_search(ScriptType.CYRILLIC, "artist", "album", ArtistContext(), tally=ProviderTally())

        mock_console.info.assert_any_call("Primary APIs failed for %s - trying fallback", "cyrillic")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_try_single_api_logs_client_not_available(
        self,
        debug_coordinator,
    ) -> None:
        coordinator, mock_console, _ = debug_coordinator

        with patch("services.api.year_search_coordinator.debug") as mock_debug:
            mock_debug.api = True
            result = await coordinator._try_single_api(
                "unknown_api",
                artist_norm="artist",
                album_norm="album",
                artist_context=ArtistContext(),
                script_type=ScriptType.LATIN,
                is_fallback=False,
                tally=ProviderTally(),
            )

        assert result is None
        mock_console.debug.assert_any_call("%s client not available, skipping", "unknown_api")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_try_single_api_logs_trying_api(
        self,
        mock_musicbrainz_client,
        debug_coordinator,
    ) -> None:
        coordinator, mock_console, _ = debug_coordinator
        mock_musicbrainz_client.get_scored_releases.return_value = [{"title": "A", "year": "2020", "score": 85}]

        with patch("services.api.year_search_coordinator.debug") as mock_debug:
            mock_debug.api = True
            await coordinator._try_single_api(
                "musicbrainz",
                artist_norm="artist",
                album_norm="album",
                artist_context=ArtistContext(),
                script_type=ScriptType.LATIN,
                is_fallback=False,
                tally=ProviderTally(),
            )

        mock_console.info.assert_any_call("Trying %s for %s text", "musicbrainz", "latin")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_try_single_api_logs_warning_on_exception(
        self,
        mock_musicbrainz_client,
        debug_coordinator,
    ) -> None:
        coordinator, mock_console, _ = debug_coordinator
        mock_musicbrainz_client.get_scored_releases.side_effect = ValueError("api boom")

        with patch("services.api.year_search_coordinator.debug") as mock_debug:
            mock_debug.api = True
            result = await coordinator._try_single_api(
                "musicbrainz",
                artist_norm="artist",
                album_norm="album",
                artist_context=ArtistContext(),
                script_type=ScriptType.CHINESE,
                is_fallback=False,
                tally=ProviderTally(),
            )

        assert result is None
        # The warning is called with the exception object; verify the format string and first two positional args
        warning_calls = [c for c in mock_console.warning.call_args_list if len(c[0]) >= 3 and c[0][0] == "%s failed for %s: %s"]
        assert len(warning_calls) == 1
        assert warning_calls[0][0][1] == "musicbrainz"
        assert warning_calls[0][0][2] == "chinese"


class TestProviderOutcomes:
    """A lookup is unavailable only when nothing was found and a provider failed."""

    @pytest.mark.asyncio
    async def test_unavailable_when_nothing_found_and_a_provider_failed(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """One failed provider and empty answers elsewhere say nothing about the album."""
        mock_musicbrainz_client.get_scored_releases.side_effect = ApiRequestError("musicbrainz", "u", "failed")
        mock_discogs_client.get_scored_releases.return_value = []
        mock_applemusic_client.get_scored_releases.return_value = []

        with pytest.raises(YearLookupUnavailableError):
            await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album")

    @pytest.mark.asyncio
    async def test_results_win_over_a_failed_provider(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """Releases from one provider are used even when another failed."""
        mock_musicbrainz_client.get_scored_releases.side_effect = ApiRequestError("musicbrainz", "u", "failed")
        mock_discogs_client.get_scored_releases.return_value = [{"title": "Album", "year": "1999", "score": 80}]
        mock_applemusic_client.get_scored_releases.return_value = []

        assert await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album")

    @pytest.mark.asyncio
    async def test_nothing_found_when_every_provider_answered(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """Empty answers from every provider are a real "nothing found"."""
        for client in (mock_musicbrainz_client, mock_discogs_client, mock_applemusic_client):
            client.get_scored_releases.return_value = []

        assert await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album") == []

    @pytest.mark.asyncio
    async def test_script_search_failure_counts(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
    ) -> None:
        """Failures in the script-optimized phase count toward an unavailable lookup."""
        for client in (mock_musicbrainz_client, mock_discogs_client, mock_applemusic_client):
            client.get_scored_releases.side_effect = ApiRequestError("p", "u", "failed")

        with pytest.raises(YearLookupUnavailableError):
            await coordinator.fetch_all_api_results("ドリカム", "アルバム", ArtistContext(), "ドリカム", "アルバム")

    @pytest.mark.asyncio
    async def test_script_phase_failure_alone_makes_the_lookup_unavailable(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
    ) -> None:
        """A provider that failed only in the script-optimized phase still leaves the lookup unknown."""
        mock_musicbrainz_client.get_scored_releases.side_effect = [ApiRequestError("musicbrainz", "u", "failed"), [], []]

        with pytest.raises(YearLookupUnavailableError):
            await coordinator.fetch_all_api_results("ドリカム", "アルバム", ArtistContext(), "ドリカム", "アルバム")

    @pytest.mark.asyncio
    async def test_discogs_without_token_is_not_queried(
        self,
        coordinator_factory: Callable[..., YearSearchCoordinator],
        mock_discogs_client: AsyncMock,
    ) -> None:
        """Discogs without a token is inactive, not failed, and never queried."""
        coordinator = coordinator_factory(discogs_enabled=False)

        assert await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album") == []
        mock_discogs_client.get_scored_releases.assert_not_called()


class TestRejectedCredentials:
    """A provider that rejects its credentials is switched off for the run, like one without a token."""

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.asyncio
    async def test_rejected_token_is_inactive_not_failed(
        self,
        *,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
        status: int,
    ) -> None:
        """Empty answers elsewhere stay "nothing found", the rejection is named once, and the provider is not asked again."""
        mock_musicbrainz_client.get_scored_releases.return_value = []
        mock_applemusic_client.get_scored_releases.return_value = []
        mock_discogs_client.get_scored_releases.side_effect = ApiRequestError("discogs", "u", f"HTTP {status}", status=status)

        with caplog.at_level(logging.WARNING):
            assert await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album") == []
            assert await coordinator.fetch_all_api_results("other", "record", ArtistContext(), "Other", "Record") == []

        assert mock_discogs_client.get_scored_releases.await_count == 1
        assert len([record for record in caplog.records if f"rejected the token (HTTP {status})" in record.getMessage()]) == 1

    @pytest.mark.asyncio
    async def test_server_error_still_makes_the_lookup_unavailable(
        self,
        coordinator: YearSearchCoordinator,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """Only a credentials rejection switches a provider off; a server error leaves the lookup unknown."""
        mock_discogs_client.get_scored_releases.side_effect = ApiRequestError("discogs", "u", "HTTP 500", status=500)

        with pytest.raises(YearLookupUnavailableError):
            await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album")

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.asyncio
    async def test_providers_without_credentials_still_fail(
        self,
        *,
        coordinator: YearSearchCoordinator,
        mock_applemusic_client: AsyncMock,
        status: int,
    ) -> None:
        """iTunes sends no credential, so its 401/403 (Apple throttles with 403) is a failure, not a rejected token."""
        mock_applemusic_client.get_scored_releases.side_effect = ApiRequestError("itunes", "u", f"HTTP {status}", status=status)

        with pytest.raises(YearLookupUnavailableError):
            await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album")
        with pytest.raises(YearLookupUnavailableError):
            await coordinator.fetch_all_api_results("other", "record", ArtistContext(), "Other", "Record")

        assert mock_applemusic_client.get_scored_releases.await_count == 2

    @pytest.mark.asyncio
    async def test_rejection_in_the_script_search_switches_discogs_off(
        self,
        coordinator: YearSearchCoordinator,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """A rejected token met in the script-optimized phase is not a failure, and the standard phase skips Discogs."""
        mock_discogs_client.get_scored_releases.side_effect = ApiRequestError("discogs", "u", "HTTP 401", status=401)

        assert await coordinator.fetch_all_api_results("ドリカム", "アルバム", ArtistContext(), "ドリカム", "アルバム") == []
        assert mock_discogs_client.get_scored_releases.await_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_rejections_are_named_once(
        self,
        coordinator: YearSearchCoordinator,
        mock_discogs_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Two searches that meet the rejection at once neither fail nor repeat the warning."""
        mock_discogs_client.get_scored_releases.side_effect = ApiRequestError("discogs", "u", "HTTP 401", status=401)

        with caplog.at_level(logging.WARNING):
            results = await asyncio.gather(
                coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album"),
                coordinator.fetch_all_api_results("other", "record", ArtistContext(), "Other", "Record"),
            )

        assert results == [[], []]
        assert len([record for record in caplog.records if "rejected the token" in record.getMessage()]) == 1

    @pytest.mark.asyncio
    async def test_queued_discogs_request_is_dropped_after_rejection(
        self,
        coordinator: YearSearchCoordinator,
        mock_discogs_client: AsyncMock,
    ) -> None:
        """A Discogs request that waited on the semaphore while its token was rejected is not sent."""
        mock_discogs_client.get_scored_releases.side_effect = ApiRequestError("discogs", "u", "HTTP 401", status=401)
        await coordinator.fetch_all_api_results("artist", "album", ArtistContext(), "Artist", "Album")

        assert await coordinator._call_api_with_proper_params(coordinator.discogs_client, "other", "record", ArtistContext()) == []
        assert mock_discogs_client.get_scored_releases.await_count == 1
