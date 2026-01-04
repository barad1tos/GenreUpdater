"""Tests for YearSearchCoordinator - coordinating API calls for release years."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.script_detection import ScriptType
from services.api.year_search_coordinator import YearSearchCoordinator


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create a test console logger."""
    return logging.getLogger("test.year_search.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create a test error logger."""
    return logging.getLogger("test.year_search.error")


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
def default_config() -> dict[str, Any]:
    """Create default configuration."""
    return {
        "year_retrieval": {
            "script_api_priorities": {
                "default": {
                    "primary": ["musicbrainz"],
                    "fallback": ["discogs"],
                },
                "cyrillic": {
                    "primary": ["discogs", "musicbrainz"],
                    "fallback": ["itunes"],
                },
            }
        }
    }


@pytest.fixture
def coordinator(
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    default_config: dict[str, Any],
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


class TestInitialization:
    """Tests for YearSearchCoordinator initialization."""

    def test_init_stores_all_parameters(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        default_config: dict[str, Any],
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
        api_list = ["discogs", "itunes"]

        result = coordinator._apply_preferred_order(api_list)

        assert result == ["discogs", "itunes"]

    def test_no_preferred_api(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        default_config: dict[str, Any],
        mock_musicbrainz_client: AsyncMock,
        mock_discogs_client: AsyncMock,
        mock_applemusic_client: AsyncMock,
        mock_release_scorer: MagicMock,
    ) -> None:
        """Test when no preferred API is set."""
        coordinator = YearSearchCoordinator(
            console_logger=console_logger,
            error_logger=error_logger,
            config=default_config,
            preferred_api="",
            musicbrainz_client=mock_musicbrainz_client,
            discogs_client=mock_discogs_client,
            applemusic_client=mock_applemusic_client,
            release_scorer=mock_release_scorer,
        )
        api_list = ["discogs", "musicbrainz"]

        result = coordinator._apply_preferred_order(api_list)

        assert result == ["discogs", "musicbrainz"]


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
        self._assert_processed_results_count(coordinator, results, 1)

    def test_handles_empty_results(self, coordinator: YearSearchCoordinator) -> None:
        """Test handling empty results."""
        results: list[Any] = [[], []]
        self._assert_processed_results_count(coordinator, results, 0)

    @staticmethod
    def _assert_processed_results_count(
        coordinator: YearSearchCoordinator,
        results: list[Any],
        expected_count: int,
    ) -> None:
        """Assert that processing results yields expected count."""
        api_order = ["musicbrainz", "discogs"]
        processed = coordinator._process_api_task_results(results, api_order, "Artist", "Album")
        assert len(processed) == expected_count


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

        results = await coordinator.fetch_all_api_results("pink floyd", "dark side", None, "Pink Floyd", "Dark Side")

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

        results = await coordinator.fetch_all_api_results("artist", "album", None, "Artist", "Album")

        assert len(results) >= 1


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

        results = await coordinator._try_single_api("musicbrainz", "artist", "album", None, ScriptType.LATIN, False)

        assert results is not None
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_unknown_api(self, coordinator: YearSearchCoordinator) -> None:
        """Test returns None for unknown API."""
        results = await coordinator._try_single_api("unknown", "artist", "album", None, ScriptType.LATIN, False)

        assert results is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_results(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
    ) -> None:
        """Test returns None when API returns empty results."""
        mock_musicbrainz_client.get_scored_releases.return_value = []

        results = await coordinator._try_single_api("musicbrainz", "artist", "album", None, ScriptType.LATIN, False)

        assert results is None

    @pytest.mark.asyncio
    async def test_handles_api_exception(
        self,
        coordinator: YearSearchCoordinator,
        mock_musicbrainz_client: AsyncMock,
    ) -> None:
        """Test handles API exception gracefully."""
        mock_musicbrainz_client.get_scored_releases.side_effect = ValueError("API error")

        results = await coordinator._try_single_api("musicbrainz", "artist", "album", None, ScriptType.LATIN, False)

        assert results is None


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
            "artist",
            "album",
            None,
            ScriptType.LATIN,
            False,
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
            "artist",
            "album",
            None,
            ScriptType.LATIN,
            False,
        )

        assert results is None


class TestLogMethods:
    """Tests for logging methods."""

    def test_log_api_error(self, coordinator: YearSearchCoordinator) -> None:
        """Test _log_api_error doesn't raise."""
        error = ValueError("Test error")
        # Should not raise
        coordinator._log_api_error("musicbrainz", "Artist", "Album", error)

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

        results = await coordinator._execute_standard_api_search("artist", "album", None, "Artist", "Album")

        assert len(results) >= 1
        mock_musicbrainz_client.get_scored_releases.assert_called_once()


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
            None,
            "Московский Исполнитель",  # Cyrillic artist
            "Альбом",
        )

        # Should have results from script-optimized search
        assert len(results) >= 1
