"""Tests for YearDeterminator private helper methods.

Tests for the determinator's cached-year, provider-query and validation steps
extracted during cognitive complexity refactoring.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.protocols import (
    CacheServiceProtocol,
    ExternalApiServiceProtocol,
    PendingVerificationServiceProtocol,
    YearLookupUnavailableError,
)
from core.debug_utils import DebugConfig
from core.models.types import TrackDict
from core.tracks.year_consistency import YearConsistencyChecker
from core.tracks.year_determination import (
    CACHE_TRUST_THRESHOLD,
    ProviderAnswer,
    YearDeterminator,
)
from core.tracks.year_fallback import YearFallbackHandler
from core.models.cache_types import AlbumCacheEntry
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


def _create_track(
    track_id: str = "1",
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    year: str | None = None,
    release_year: str | None = None,
    date_added: str | None = None,
) -> TrackDict:
    """Create a test TrackDict with specified values."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        year=year,
        release_year=release_year,
        date_added=date_added,
    )


def _create_mock_cache_service() -> MagicMock:
    """Create a mock cache service."""
    service = MagicMock()
    service.get_album_year_from_cache = AsyncMock(return_value=None)
    service.get_album_year_entry_from_cache = AsyncMock(return_value=None)
    service.store_album_year_in_cache = AsyncMock()
    return service


def _create_mock_external_api() -> MagicMock:
    """Create a mock external API service."""
    api = MagicMock()
    api.get_album_year = AsyncMock(return_value=(None, False, 0, {}))
    return api


def _create_mock_pending_verification() -> MagicMock:
    """Create a mock pending verification service."""
    service = MagicMock()
    service.mark_for_verification = AsyncMock()
    service.get_entry = AsyncMock(return_value=None)
    service.is_verification_needed = AsyncMock(return_value=False)
    return service


def _create_mock_consistency_checker() -> MagicMock:
    """Create a mock consistency checker whose local hints say nothing."""
    checker = MagicMock()
    checker.get_majority_year = MagicMock(return_value=None)
    checker.get_most_common_year = MagicMock(return_value=None)
    checker.get_consensus_release_year = MagicMock(return_value=None)
    return checker


def _real_checker() -> YearConsistencyChecker:
    """The checker as the retriever ships it, so the track fixtures are really counted."""
    return YearConsistencyChecker(console_logger=logging.getLogger("test.checker"))


def _create_mock_fallback_handler() -> MagicMock:
    """Create a mock fallback handler."""
    handler = MagicMock()
    handler.apply_year_fallback = AsyncMock(return_value=None)
    return handler


def _create_year_determinator(
    *,
    cache_service: MagicMock | None = None,
    external_api: MagicMock | None = None,
    pending_verification: MagicMock | None = None,
    consistency_checker: MagicMock | None = None,
    real_checker: YearConsistencyChecker | None = None,
    fallback_handler: MagicMock | None = None,
    config: AppConfig | None = None,
) -> YearDeterminator:
    """Create YearDeterminator with mock dependencies."""
    return YearDeterminator(
        cache_service=cast(CacheServiceProtocol, cast(object, cache_service or _create_mock_cache_service())),
        external_api=cast(ExternalApiServiceProtocol, cast(object, external_api or _create_mock_external_api())),
        pending_verification=cast(
            PendingVerificationServiceProtocol,
            cast(object, pending_verification or _create_mock_pending_verification()),
        ),
        consistency_checker=real_checker or cast(YearConsistencyChecker, consistency_checker or _create_mock_consistency_checker()),
        fallback_handler=cast(YearFallbackHandler, fallback_handler or _create_mock_fallback_handler()),
        console_logger=logging.getLogger("test.console"),
        error_logger=logging.getLogger("test.error"),
        config=config or create_test_app_config(),
    )


@pytest.mark.unit
class TestTryCachedYear:
    """The album-year cache is the only local answer; it must be fresh and confident."""

    @pytest.mark.asyncio
    async def test_returns_cached_year_with_high_confidence(self) -> None:
        cache_service = _create_mock_cache_service()
        cache_entry = AlbumCacheEntry(artist="Artist", album="Album", year="2019", timestamp=0.0, confidence=CACHE_TRUST_THRESHOLD)
        cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=cache_entry)
        determinator = _create_year_determinator(cache_service=cache_service)

        assert await determinator._try_cached_year("Artist", "Album") == "2019"
        cache_service.get_album_year_entry_from_cache.assert_called_once_with("Artist", "Album")

    @pytest.mark.asyncio
    async def test_ignores_cached_year_with_low_confidence(self) -> None:
        cache_service = _create_mock_cache_service()
        cache_entry = AlbumCacheEntry(artist="Artist", album="Album", year="2019", timestamp=0.0, confidence=CACHE_TRUST_THRESHOLD - 1)
        cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=cache_entry)
        determinator = _create_year_determinator(cache_service=cache_service)

        assert await determinator._try_cached_year("Artist", "Album") is None

    @pytest.mark.asyncio
    async def test_returns_none_without_an_entry(self) -> None:
        assert await _create_year_determinator()._try_cached_year("Artist", "Album") is None


class TestProvidersDecide:
    """A library-internal year is a hint; the providers' verdict is the answer."""

    @staticmethod
    def _passthrough_fallback() -> MagicMock:
        handler = _create_mock_fallback_handler()
        handler.apply_year_fallback = AsyncMock(side_effect=lambda proposed_year, *_, **__: proposed_year)
        return handler

    @pytest.mark.asyncio
    async def test_tracks_that_disagree_ask_the_providers(self) -> None:
        """13 of 14 tracks at 2019 (Apple's catalog) and one at 2016: the providers say 2016."""
        checker = _real_checker()
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("2016", True, 90, {"2016": 90}))
        determinator = _create_year_determinator(real_checker=checker, external_api=external_api, fallback_handler=self._passthrough_fallback())
        tracks = [_create_track(year="2019") for _ in range(13)] + [_create_track(year="2016")]

        with patch.object(checker, "get_majority_year", wraps=checker.get_majority_year) as get_majority_year:
            assert await determinator.determine_album_year("In Flames", "Battles", tracks) == "2016"
        provider_call = external_api.get_album_year.await_args
        assert provider_call is not None
        assert provider_call.kwargs["current_library_year"] == "2019"
        get_majority_year.assert_not_called()

    @pytest.mark.asyncio
    async def test_apples_release_date_is_a_hint_not_a_verdict(self) -> None:
        """One track, library 1995, Apple release date 1991, providers 1996: 1996, with 1991 handed to the fallback."""
        checker = _create_mock_consistency_checker()
        checker.get_most_common_year = MagicMock(return_value="1995")
        checker.get_consensus_release_year = MagicMock(return_value="1991")
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("1996", True, 85, {"1996": 85}))
        fallback = self._passthrough_fallback()
        determinator = _create_year_determinator(consistency_checker=checker, external_api=external_api, fallback_handler=fallback)

        assert (
            await determinator.determine_album_year("Royal Crown Revue", "Mugzy's Move", [_create_track(year="1995", release_year="1991")]) == "1996"
        )
        assert fallback.apply_year_fallback.await_args.kwargs["release_year"] == "1991"

    @pytest.mark.asyncio
    async def test_majority_fills_outliers_when_no_provider_knows_the_album(self) -> None:
        """Three tracks at 2016 and one at 2026, no provider year: the outlier gets 2016 without the fallback rules."""
        fallback = _create_mock_fallback_handler()
        determinator = _create_year_determinator(real_checker=_real_checker(), fallback_handler=fallback)
        tracks = [_create_track(year="2016") for _ in range(3)] + [_create_track(year="2026")]

        assert await determinator.determine_album_year("Artist", "Album", tracks) == "2016"
        fallback.apply_year_fallback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_majority_log_prints_names_verbatim(self, rich_console_logger: tuple[logging.Logger, io.StringIO]) -> None:
        """Names Rich would read as markup appear as they are on the majority line, and the write still happens."""
        console_logger, console_output = rich_console_logger
        console_logger.setLevel(logging.INFO)
        determinator = _create_year_determinator(real_checker=_real_checker(), fallback_handler=_create_mock_fallback_handler())
        determinator.console_logger = console_logger
        tracks = [_create_track(year="2016") for _ in range(3)] + [_create_track(year="2026")]

        assert await determinator.determine_album_year("Artist", "Live [/edit]", tracks) == "2016"
        assert "'Artist - Live [/edit]'" in console_output.getvalue()

    @pytest.mark.asyncio
    async def test_a_current_year_majority_on_tracks_added_earlier_is_not_applied(self) -> None:
        """This year's date on most tracks of an older album is Apple's placeholder, so it must not spread to the rest."""
        this_year = str(datetime.now(UTC).year)
        checker = _create_mock_consistency_checker()
        checker.get_majority_year = MagicMock(return_value=this_year)
        determinator = _create_year_determinator(consistency_checker=checker, external_api=_create_mock_external_api())
        tracks = [_create_track(year=this_year, date_added="2024-03-01 00:00:00") for _ in range(8)]
        tracks += [_create_track(year="2004", date_added="2024-03-01 00:00:00") for _ in range(2)]

        assert await determinator.determine_album_year("Artist", "Album", tracks) is None

    @pytest.mark.asyncio
    async def test_a_current_year_majority_with_one_older_track_is_not_applied(self) -> None:
        """The earliest date added decides: one track received in 2024 makes this year a placeholder."""
        this_year = str(datetime.now(UTC).year)
        checker = _create_mock_consistency_checker()
        checker.get_majority_year = MagicMock(return_value=this_year)
        determinator = _create_year_determinator(consistency_checker=checker, external_api=_create_mock_external_api())
        tracks = [_create_track(year=this_year, date_added=f"{this_year}-03-01 00:00:00") for _ in range(7)]
        tracks += [_create_track(year=this_year, date_added="2024-03-01 00:00:00"), _create_track(year="", date_added=f"{this_year}-03-01 00:00:00")]

        assert await determinator.determine_album_year("Artist", "Album", tracks) is None

    @pytest.mark.asyncio
    async def test_a_current_year_majority_without_dates_is_not_applied(self) -> None:
        """Without a date added, nothing vouches for this year, so the placeholder reading stands."""
        this_year = str(datetime.now(UTC).year)
        checker = _create_mock_consistency_checker()
        checker.get_majority_year = MagicMock(return_value=this_year)
        determinator = _create_year_determinator(consistency_checker=checker, external_api=_create_mock_external_api())
        tracks = [_create_track(year=this_year) for _ in range(8)] + [_create_track(year="2004") for _ in range(2)]

        assert await determinator.determine_album_year("Artist", "Album", tracks) is None

    @pytest.mark.asyncio
    async def test_a_current_year_majority_on_tracks_added_this_year_is_applied(self) -> None:
        """An album added this year can really be from this year."""
        this_year = str(datetime.now(UTC).year)
        checker = _create_mock_consistency_checker()
        checker.get_majority_year = MagicMock(return_value=this_year)
        determinator = _create_year_determinator(consistency_checker=checker, external_api=_create_mock_external_api())
        tracks = [_create_track(year=this_year, date_added=f"{this_year}-03-01 00:00:00") for _ in range(8)]
        tracks += [_create_track(year="", date_added=f"{this_year}-03-01 00:00:00") for _ in range(2)]

        assert await determinator.determine_album_year("Artist", "Album", tracks) == this_year

    @pytest.mark.asyncio
    async def test_a_failed_lookup_leaves_the_album_for_the_next_run(self) -> None:
        """A lookup that blew up is no provider verdict: nothing is written, not even the majority."""
        checker = _create_mock_consistency_checker()
        checker.get_majority_year = MagicMock(return_value="2016")
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(side_effect=OSError("boom"))
        determinator = _create_year_determinator(consistency_checker=checker, external_api=external_api)

        with pytest.raises(YearLookupUnavailableError):
            await determinator.determine_album_year("Artist", "Album", [_create_track(year="2016"), _create_track(year="2026")])
        checker.get_majority_year.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_rejected_provider_year_is_not_replaced_by_the_majority(self) -> None:
        checker = _create_mock_consistency_checker()
        checker.get_majority_year = MagicMock(return_value="2016")
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("1999", False, 20, {"1999": 20}))
        determinator = _create_year_determinator(
            consistency_checker=checker, external_api=external_api, fallback_handler=_create_mock_fallback_handler()
        )

        assert await determinator.determine_album_year("Artist", "Album", [_create_track(year="2016"), _create_track(year="2026")]) is None
        checker.get_majority_year.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_fresh_confident_cache_entry_answers_first(self) -> None:
        cache_service = _create_mock_cache_service()
        cache_entry = AlbumCacheEntry(artist="Artist", album="Album", year="2010", timestamp=0.0, confidence=CACHE_TRUST_THRESHOLD)
        cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=cache_entry)
        external_api = _create_mock_external_api()
        determinator = _create_year_determinator(cache_service=cache_service, external_api=external_api)

        assert await determinator.determine_album_year("Artist", "Album", [_create_track(year="2012")]) == "2010"
        external_api.get_album_year.assert_not_awaited()


class TestValidateProviderYear:
    """The fallback rules judge a provider answer; a confident result is cached."""

    @pytest.mark.asyncio
    async def test_returns_validated_year_from_api(self) -> None:
        """Should return validated year from API and cache it."""
        cache_service = _create_mock_cache_service()
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("2021", True, 95, {"2021": 95}))

        fallback_handler = _create_mock_fallback_handler()
        fallback_handler.apply_year_fallback = AsyncMock(return_value="2021")

        determinator = _create_year_determinator(
            cache_service=cache_service,
            external_api=external_api,
            fallback_handler=fallback_handler,
        )
        tracks = [_create_track()]

        result = await determinator._validate_provider_year("Artist", "Album", tracks, ProviderAnswer("2021", True, 95, {"2021": 95}))

        assert result == "2021"
        cache_service.store_album_year_in_cache.assert_called_once_with(
            "Artist",
            "Album",
            "2021",
            confidence=95,
        )

    @pytest.mark.asyncio
    async def test_a_year_the_fallback_substituted_is_not_cached(self) -> None:
        """When the fallback keeps the library's year over the providers', nothing is cached under the providers' confidence."""
        cache_service = _create_mock_cache_service()
        fallback = _create_mock_fallback_handler()
        fallback.apply_year_fallback = AsyncMock(return_value="2019")
        determinator = _create_year_determinator(cache_service=cache_service, fallback_handler=fallback)
        tracks = [_create_track(year="2019")]

        result = await determinator._validate_provider_year("Artist", "Album", tracks, ProviderAnswer("2004", False, 60, {"2004": 60}))

        assert result == "2019"
        cache_service.store_album_year_in_cache.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_none_when_fallback_rejects(self) -> None:
        """Should return None when fallback handler rejects the year."""
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("2021", False, 50, {"2021": 50}))

        fallback_handler = _create_mock_fallback_handler()
        fallback_handler.apply_year_fallback = AsyncMock(return_value=None)

        cache_service = _create_mock_cache_service()

        determinator = _create_year_determinator(
            cache_service=cache_service,
            external_api=external_api,
            fallback_handler=fallback_handler,
        )
        tracks = [_create_track()]

        result = await determinator._validate_provider_year("Artist", "Album", tracks, ProviderAnswer("2021", False, 50, {"2021": 50}))

        assert result is None
        # Should NOT cache rejected year
        cache_service.store_album_year_in_cache.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(RuntimeError("API error"), id="RuntimeError"),
            pytest.param(OSError("Network error"), id="OSError"),
            pytest.param(ValueError("Invalid data"), id="ValueError"),
        ],
    )
    @pytest.mark.asyncio
    async def test_api_failure_is_logged_and_raised(
        self,
        error: Exception,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An API failure leaves the lookup unavailable; the error log gets its traceback and the console a one-line warning, even with year debugging off."""
        # DebugConfig() also reads DEBUG_YEAR and DEBUG_ALL, so switch year debugging off explicitly
        year_debugging_off = DebugConfig()
        year_debugging_off.year = False
        monkeypatch.setattr("core.tracks.year_determination.debug", year_debugging_off)
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(side_effect=error)
        determinator = _create_year_determinator(external_api=external_api)

        with caplog.at_level(logging.WARNING), pytest.raises(YearLookupUnavailableError) as raised:
            await determinator._query_providers("Artist", "Album", None)

        assert raised.value.__cause__ is error
        error_records = [record for record in caplog.records if record.name == "test.error"]
        assert len(error_records) == 1
        logged_exception = error_records[0].exc_info
        assert logged_exception is not None
        assert logged_exception[1] is error
        assert logged_exception[2] is not None  # the traceback itself, not only the exception
        assert "'Artist - Album'" in error_records[0].getMessage()
        console_records = [record for record in caplog.records if record.name == "test.console"]
        assert [(record.levelno, record.exc_info) for record in console_records] == [(logging.WARNING, None)]
        assert type(error).__name__ in console_records[0].getMessage()

    @pytest.mark.asyncio
    async def test_api_failure_console_line_prints_names_verbatim(
        self,
        rich_console_logger: tuple[logging.Logger, io.StringIO],
    ) -> None:
        """Names Rich would read as markup appear as they are, and the failed lookup is raised as unavailable."""
        console_logger, console_output = rich_console_logger
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(side_effect=OSError("Network error"))
        determinator = _create_year_determinator(external_api=external_api)
        determinator.console_logger = console_logger

        with pytest.raises(YearLookupUnavailableError):
            await determinator._query_providers("Artist [live]", "Mixes [/edit]", None)

        assert "'Artist [live] - Mixes [/edit]'" in console_output.getvalue()

    @pytest.mark.asyncio
    async def test_passes_the_library_year_to_the_providers(self) -> None:
        """The most common library year reaches the providers as a hint."""
        external_api = _create_mock_external_api()
        determinator = _create_year_determinator(external_api=external_api)

        await determinator._query_providers("Artist", "Album", "2019")

        external_api.get_album_year.assert_called_once()
        call_kwargs = external_api.get_album_year.call_args
        assert call_kwargs[1]["current_library_year"] == "2019"

    @pytest.mark.asyncio
    async def test_passes_fallback_params_correctly(self) -> None:
        """Should pass all parameters to fallback handler."""
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("2021", True, 95, {"2021": 95, "2020": 80}))

        fallback_handler = _create_mock_fallback_handler()

        determinator = _create_year_determinator(
            external_api=external_api,
            fallback_handler=fallback_handler,
        )
        tracks = [_create_track()]

        await determinator._validate_provider_year("Artist", "Album", tracks, ProviderAnswer("2021", True, 95, {"2021": 95, "2020": 80}))

        fallback_handler.apply_year_fallback.assert_called_once()
        call_kwargs = fallback_handler.apply_year_fallback.call_args[1]
        assert call_kwargs["proposed_year"] == "2021"
        assert call_kwargs["is_definitive"] is True
        assert call_kwargs["confidence_score"] == 95
        assert call_kwargs["artist"] == "Artist"
        assert call_kwargs["album"] == "Album"
        assert call_kwargs["year_scores"] == {"2021": 95, "2020": 80}


class TestQueryProviders:
    """What the providers said, before any judgement."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_provider_knows_the_album(self) -> None:
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=(None, False, 0, {}))
        determinator = _create_year_determinator(external_api=external_api)

        assert await determinator._query_providers("Artist", "Album", None) is None

    @pytest.mark.asyncio
    async def test_returns_the_answer_as_given(self) -> None:
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("2021", True, 95, {"2021": 95}))
        determinator = _create_year_determinator(external_api=external_api)

        assert await determinator._query_providers("Artist", "Album", "2020") == ProviderAnswer("2021", True, 95, {"2021": 95})


class TestFetchFromApiUnavailable:
    """An album no provider answered for is left alone for the next run."""

    @pytest.mark.asyncio
    async def test_unavailable_lookup_propagates_without_fallback_or_cache(self) -> None:
        """The determinator passes an unavailable lookup up instead of reading it as an album without a year."""
        cache_service = _create_mock_cache_service()
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(side_effect=YearLookupUnavailableError("no provider"))
        fallback_handler = _create_mock_fallback_handler()
        determinator = _create_year_determinator(cache_service=cache_service, external_api=external_api, fallback_handler=fallback_handler)

        with pytest.raises(YearLookupUnavailableError):
            await determinator._query_providers("Artist", "Album", "1999")

        fallback_handler.apply_year_fallback.assert_not_called()
        cache_service.store_album_year_in_cache.assert_not_called()

    def test_unavailable_is_not_an_os_error(self) -> None:
        """The clients' and services' broad except (OSError, ...) handlers must never read an unavailable lookup as "no year"."""
        assert not issubclass(YearLookupUnavailableError, OSError)
