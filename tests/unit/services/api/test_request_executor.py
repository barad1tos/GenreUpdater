"""Tests for ApiRequestExecutor - HTTP request execution with retry and caching."""

import json
import logging
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from services.api.request_executor import (
    API_RESPONSE_LOG_LIMIT,
    HTTP_SERVER_ERROR,
    HTTP_TOO_MANY_REQUESTS,
    WAIT_TIME_LOG_THRESHOLD,
    ApiRequestError,
    ApiRequestExecutor,
    TransientRequestError,
)
from tests.mocks.protocol_mocks import MockCacheService

if TYPE_CHECKING:
    from core.models.protocols import CacheServiceProtocol
    from services.api.api_base import ApiRateLimiter

# Test API token (not a real credential)
TEST_API_TOKEN = "test_token"  # noqa: S105


@pytest.fixture
def mock_cache_service() -> AsyncMock:
    """Create a mock cache service."""
    service = AsyncMock()
    service.get_async = AsyncMock(return_value=None)
    service.set_async = AsyncMock()
    service.invalidate = MagicMock()
    return service


@pytest.fixture
def mock_rate_limiter() -> AsyncMock:
    """Create a mock rate limiter."""
    limiter = AsyncMock()
    limiter.acquire = AsyncMock(return_value=0.0)
    limiter.release = MagicMock()
    return limiter


@pytest.fixture
def mock_rate_limiters(mock_rate_limiter: AsyncMock) -> dict[str, "ApiRateLimiter"]:
    """Create dict of mock rate limiters for all APIs."""
    return cast(
        dict[str, "ApiRateLimiter"],
        {
            "discogs": mock_rate_limiter,
            "musicbrainz": mock_rate_limiter,
            "itunes": mock_rate_limiter,
        },
    )


@pytest.fixture
def executor(
    mock_cache_service: AsyncMock,
    mock_rate_limiters: dict[str, "ApiRateLimiter"],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> ApiRequestExecutor:
    """Create an ApiRequestExecutor instance."""
    return ApiRequestExecutor(
        cache_service=cast("CacheServiceProtocol", cast(object, mock_cache_service)),
        rate_limiters=mock_rate_limiters,
        console_logger=console_logger,
        error_logger=error_logger,
        user_agent="TestAgent/1.0",
        discogs_token=TEST_API_TOKEN,
        cache_ttl_seconds=86400,
        default_max_retries=3,
        default_retry_delay=0.01,
    )


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock aiohttp session."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.closed = False
    session.headers = {"User-Agent": "TestAgent/1.0"}
    session.timeout = aiohttp.ClientTimeout(total=30)
    return session


class TestInitialization:
    """Tests for ApiRequestExecutor initialization."""

    def test_init_with_all_params(
        self,
        mock_cache_service: AsyncMock,
        mock_rate_limiters: dict[str, "ApiRateLimiter"],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test initialization with all parameters."""
        executor = ApiRequestExecutor(
            cache_service=cast("CacheServiceProtocol", cast(object, mock_cache_service)),
            rate_limiters=mock_rate_limiters,
            console_logger=console_logger,
            error_logger=error_logger,
            user_agent="TestAgent/1.0",
            discogs_token=TEST_API_TOKEN,
            cache_ttl_seconds=604800,
            default_max_retries=5,
            default_retry_delay=1.0,
        )

        assert executor.user_agent == "TestAgent/1.0"
        assert executor.discogs_token == TEST_API_TOKEN
        assert executor.cache_ttl_seconds == 604800
        assert executor.default_max_retries == 5
        assert executor.default_retry_delay == 1.0
        assert executor.session is None

    def test_init_request_counts(self, executor: ApiRequestExecutor) -> None:
        """Test request counts are initialized."""
        assert "discogs" in executor.request_counts
        assert "musicbrainz" in executor.request_counts
        assert "itunes" in executor.request_counts
        assert all(v == 0 for v in executor.request_counts.values())

    def test_init_api_call_durations(self, executor: ApiRequestExecutor) -> None:
        """Test API call durations are initialized."""
        assert "discogs" in executor.api_call_durations
        assert all(isinstance(v, list) for v in executor.api_call_durations.values())


class TestSetSession:
    """Tests for set_session method."""

    def test_set_session(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test setting session."""
        executor.set_session(mock_session)
        assert executor.session is mock_session

    def test_set_session_to_none(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test setting session to None."""
        executor.set_session(mock_session)
        executor.set_session(None)
        assert executor.session is None


class TestBuildCacheKey:
    """Tests for _build_cache_key static method."""

    def test_build_cache_key_basic(self) -> None:
        """Test building cache key with basic parameters."""
        key = ApiRequestExecutor._build_cache_key(
            "musicbrainz",
            "https://api.example.com/search",
            {"artist": "Test Artist"},
        )

        assert isinstance(key, str)
        assert key.startswith("api_response_musicbrainz_")
        assert "musicbrainz" in key

    def test_build_cache_key_no_params(self) -> None:
        """Test building cache key without params."""
        key = ApiRequestExecutor._build_cache_key(
            "discogs",
            "https://api.example.com",
            None,
        )

        assert isinstance(key, str)
        assert "discogs" in key

    def test_build_cache_key_deterministic(self) -> None:
        """Test cache key is deterministic."""
        key1 = ApiRequestExecutor._build_cache_key("discogs", "https://api.example.com", {"param": "value"})
        key2 = ApiRequestExecutor._build_cache_key("discogs", "https://api.example.com", {"param": "value"})

        assert key1 == key2

    def test_build_cache_key_different_for_different_params(self) -> None:
        """Test different params produce different keys."""
        key1 = ApiRequestExecutor._build_cache_key("api", "https://api.example.com", {"param": "value1"})
        key2 = ApiRequestExecutor._build_cache_key("api", "https://api.example.com", {"param": "value2"})

        assert key1 != key2


class TestCheckCache:
    """Tests for _check_cache method."""

    @pytest.mark.asyncio
    async def test_check_cache_miss(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test cache miss returns None."""
        mock_cache_service.get_async.return_value = None

        result = await executor._check_cache("key", "api", "url")

        assert result is None

    @pytest.mark.asyncio
    async def test_check_cache_hit(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test cache hit returns cached data."""
        cached_data = {"results": [{"title": "Album"}]}
        mock_cache_service.get_async.return_value = cached_data

        result = await executor._check_cache("key", "api", "url")

        assert result == cached_data

    @pytest.mark.asyncio
    async def test_check_cache_treats_empty_dict_as_miss(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """A cached {} is a failed request stored by an earlier version, so the API is asked again."""
        mock_cache_service.get_async.return_value = {}

        result = await executor._check_cache("key", "api", "url")

        assert result is None

    @pytest.mark.asyncio
    async def test_check_cache_invalid_type_invalidates(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test invalid cached type is invalidated."""
        mock_cache_service.get_async.return_value = "not a dict"

        result = await executor._check_cache("key", "api", "url")

        assert result is None
        mock_cache_service.invalidate.assert_called_once_with("key")


class TestCacheResult:
    """Tests for _cache_result method."""

    @pytest.mark.asyncio
    async def test_cache_result_with_data(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test caching result with data."""
        result = {"data": "value"}
        await executor._cache_result("key", result)

        mock_cache_service.set_async.assert_called_once()
        args = mock_cache_service.set_async.call_args
        assert args[0][0] == "key"
        assert args[0][1] == result
        assert args[1]["ttl"] == 86400  # 1 day in seconds

    @pytest.mark.asyncio
    async def test_cache_result_skips_failed_request(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """A failed request is not cached, so the next lookup reaches the API."""
        await executor._cache_result("key", None)

        mock_cache_service.set_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_result_skips_empty_body(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """An empty body is not cached either, so a cached {} can only be a failure an earlier version stored."""
        await executor._cache_result("key", {})

        mock_cache_service.set_async.assert_not_called()


class TestFailedRequestCaching:
    """A failed request must not reach the cache, and a {} record from an earlier version must not hide the API."""

    @pytest.fixture
    def mock_cache_service(self) -> MockCacheService:
        """Back the executor with the dict-backed cache fake, so a test sees what execute_request stored."""
        return MockCacheService()

    @pytest.mark.asyncio
    async def test_failed_request_is_asked_again(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
        mock_cache_service: MockCacheService,
    ) -> None:
        """A failed request leaves nothing in the cache, so the next call reaches the API and caches its answer."""
        executor.set_session(mock_session)
        url = "https://api.discogs.com/database/search"
        answer = {"results": [{"title": "Album"}]}

        failure = ApiRequestError("discogs", url, "failed after 4 attempts")
        with patch.object(executor, "_execute_with_retry", new_callable=AsyncMock, side_effect=[failure, answer]) as request:
            with pytest.raises(ApiRequestError):
                await executor.execute_request("discogs", url)
            assert mock_cache_service.storage == {}
            assert await executor.execute_request("discogs", url) == answer

        assert request.await_count == 2
        assert list(mock_cache_service.storage.values()) == [answer]

    @pytest.mark.asyncio
    async def test_legacy_empty_record_heals(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
        mock_cache_service: MockCacheService,
    ) -> None:
        """A {} record is asked again: a new failure leaves it as it is, and a real answer replaces it."""
        executor.set_session(mock_session)
        url = "https://musicbrainz.org/ws/2/release-group"
        mock_cache_service.storage[executor._build_cache_key("musicbrainz", url, None)] = {}
        no_matches = {"count": 0, "release-groups": []}

        failure = ApiRequestError("musicbrainz", url, "failed after 4 attempts")
        with patch.object(executor, "_execute_with_retry", new_callable=AsyncMock, side_effect=[failure, no_matches]):
            with pytest.raises(ApiRequestError):
                await executor.execute_request("musicbrainz", url)
            assert list(mock_cache_service.storage.values()) == [{}]
            assert await executor.execute_request("musicbrainz", url) == no_matches

        assert list(mock_cache_service.storage.values()) == [no_matches]


class TestRequestOutcomes:
    """Each response maps to one outcome: an answer, a definite "not found", or a failure after the retry budget."""

    @staticmethod
    def respond(status: int, *, json_body: object = None, headers: dict[str, str] | None = None) -> MagicMock:
        """Build a fake aiohttp response usable as `async with session.get(...)`."""
        response = MagicMock()
        response.status = status
        response.ok = status < 400
        response.headers = {"Content-Type": "application/json", **(headers or {})}
        response.text = AsyncMock(return_value=json.dumps(json_body))
        response.json = AsyncMock(return_value=json_body)
        response.request_info = MagicMock(headers={})
        response.history = ()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        return context

    @pytest.fixture
    def sleeps(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        """Record backoff sleeps instead of waiting."""
        delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            """Record the delay without waiting."""
            delays.append(delay)

        monkeypatch.setattr("services.api.request_executor.asyncio.sleep", fake_sleep)
        return delays

    @pytest.mark.asyncio
    async def test_answer(self, executor: ApiRequestExecutor, mock_session: MagicMock) -> None:
        """A 200 with a JSON object is the answer."""
        mock_session.get = MagicMock(return_value=self.respond(200, json_body={"count": 0}))
        executor.set_session(mock_session)

        assert await executor.execute_request("musicbrainz", "https://mb.example/ws") == {"count": 0}

    @pytest.mark.asyncio
    async def test_not_found_is_a_definite_answer(self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float]) -> None:
        """A 404 says the resource does not exist: None, asked once, no backoff."""
        mock_session.get = MagicMock(return_value=self.respond(404, json_body={"message": "not found"}))
        executor.set_session(mock_session)

        assert await executor.execute_request("discogs", "https://api.discogs.com/masters/1") is None
        assert mock_session.get.call_count == 1
        assert not sleeps

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_other_client_errors_fail_at_once(
        self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float], status: int
    ) -> None:
        """A client error other than 404 will not change on a retry, so it fails on the first attempt, naming the query."""
        mock_session.get = MagicMock(return_value=self.respond(status, json_body={}))
        executor.set_session(mock_session)

        with pytest.raises(ApiRequestError) as raised:
            await executor.execute_request("discogs", "https://api.discogs.com/database/search", params={"q": "abbey road"})

        assert raised.value.status == status
        assert "q=abbey" in raised.value.url
        assert mock_session.get.call_count == 1
        assert not sleeps

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_transient_statuses_retry_then_fail(
        self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float], status: int
    ) -> None:
        """A rate limit or server error is retried with backoff, then fails once the budget is spent."""
        mock_session.get = MagicMock(return_value=self.respond(status, json_body={}))
        executor.set_session(mock_session)

        with pytest.raises(ApiRequestError) as raised:
            await executor.execute_request("musicbrainz", "https://mb.example/ws")

        assert raised.value.status == status
        assert mock_session.get.call_count == executor.default_max_retries + 1
        assert len(sleeps) == executor.default_max_retries

    @pytest.mark.asyncio
    async def test_retry_after_is_honoured(self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float]) -> None:
        """A 429 that names its wait gets exactly that wait before the next attempt."""
        mock_session.get = MagicMock(
            side_effect=[self.respond(429, json_body={}, headers={"Retry-After": "7"}), self.respond(200, json_body={"ok": True})]
        )
        executor.set_session(mock_session)

        assert await executor.execute_request("discogs", "https://api.discogs.com/database/search") == {"ok": True}
        assert sleeps == [7.0]

    @pytest.mark.asyncio
    async def test_retry_after_date_is_honoured(self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float]) -> None:
        """A 429 that names its wait as an HTTP date gets the time left until that date."""
        resume_at = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
        mock_session.get = MagicMock(
            side_effect=[self.respond(429, json_body={}, headers={"Retry-After": resume_at}), self.respond(200, json_body={"ok": True})]
        )
        executor.set_session(mock_session)

        assert await executor.execute_request("discogs", "https://api.discogs.com/database/search") == {"ok": True}
        assert len(sleeps) == 1
        assert 25.0 <= sleeps[0] <= 30.0

    @pytest.mark.asyncio
    async def test_zero_retry_after_still_backs_off(self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float]) -> None:
        """A provider that says "retry now" while rate limiting still gets the backoff, not an instant retry."""
        mock_session.get = MagicMock(
            side_effect=[self.respond(503, json_body={}, headers={"Retry-After": "0"}), self.respond(200, json_body={"ok": True})]
        )
        executor.set_session(mock_session)

        assert await executor.execute_request("musicbrainz", "https://musicbrainz.org/ws/2/release-group/") == {"ok": True}
        assert len(sleeps) == 1
        assert sleeps[0] >= 0.008  # base delay 0.01 with the lowest jitter

    @pytest.mark.asyncio
    async def test_itunes_throttling_403_is_retried(self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float]) -> None:
        """iTunes answers a burst with 403 rather than 429, so its 403 is retried with backoff like a rate limit."""
        mock_session.get = MagicMock(side_effect=[self.respond(403, json_body={}), self.respond(200, json_body={"results": []})])
        executor.set_session(mock_session)

        assert await executor.execute_request("itunes", "https://itunes.apple.com/search", params={"term": "rock"}) == {"results": []}
        assert len(sleeps) == 1

    @pytest.mark.asyncio
    async def test_not_found_is_an_answer_even_when_its_body_breaks(
        self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float]
    ) -> None:
        """A 404 says the resource does not exist before its body is read, so a broken body does not turn it into a retry."""
        missing = self.respond(404, json_body={})
        missing.__aenter__.return_value.text = AsyncMock(side_effect=aiohttp.ClientPayloadError("connection dropped"))
        mock_session.get = MagicMock(return_value=missing)
        executor.set_session(mock_session)

        assert await executor.execute_request("discogs", "https://api.discogs.com/masters/1") is None
        assert not sleeps

    @pytest.mark.asyncio
    async def test_answer_is_cached_for_the_configured_ttl(
        self, executor: ApiRequestExecutor, mock_session: MagicMock, mock_cache_service: AsyncMock
    ) -> None:
        """An answer is stored for the executor's TTL in seconds, so an empty one is asked again once it runs out."""
        mock_session.get = MagicMock(return_value=self.respond(200, json_body={"results": []}))
        executor.set_session(mock_session)

        await executor.execute_request("discogs", "https://api.discogs.com/database/search", params={"q": "abbey road"})

        assert mock_cache_service.set_async.await_args.kwargs["ttl"] == executor.cache_ttl_seconds

    @pytest.mark.asyncio
    async def test_answers_cached_under_the_old_key_are_not_read(
        self, executor: ApiRequestExecutor, mock_session: MagicMock, mock_cache_service: AsyncMock
    ) -> None:
        """Answers stored before the TTL change expire in 2126, so the cache key moved and they are no longer read."""
        mock_cache_service.get_async = AsyncMock(side_effect=lambda key: {"stale": True} if str(key).startswith("api_request_") else None)
        mock_session.get = MagicMock(return_value=self.respond(200, json_body={"fresh": True}))
        executor.set_session(mock_session)

        assert await executor.execute_request("discogs", "https://api.discogs.com/database/search") == {"fresh": True}

    @pytest.mark.asyncio
    async def test_dropped_body_is_retried(self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float]) -> None:
        """A connection dropped while the body is read is transient, so the request is sent again."""
        dropped = self.respond(200, json_body={})
        dropped.__aenter__.return_value.json = AsyncMock(side_effect=aiohttp.ClientPayloadError("connection dropped"))
        mock_session.get = MagicMock(side_effect=[dropped, self.respond(200, json_body={"ok": True})])
        executor.set_session(mock_session)

        assert await executor.execute_request("musicbrainz", "https://musicbrainz.org/ws/2/release-group/") == {"ok": True}
        assert len(sleeps) == 1

    @pytest.mark.asyncio
    async def test_timeout_retries_then_fails(self, executor: ApiRequestExecutor, mock_session: MagicMock, sleeps: list[float]) -> None:
        """Timeouts are retried, and the final failure keeps the timeout as its cause."""
        mock_session.get = MagicMock(side_effect=TimeoutError("slow"))
        executor.set_session(mock_session)

        with pytest.raises(ApiRequestError) as raised:
            await executor.execute_request("itunes", "https://itunes.example/search")

        assert isinstance(raised.value.__cause__, TimeoutError)
        assert len(sleeps) == executor.default_max_retries

    @pytest.mark.asyncio
    async def test_non_json_answer_is_a_failure(self, executor: ApiRequestExecutor, mock_session: MagicMock) -> None:
        """An HTML page in place of JSON is not an answer."""
        context = self.respond(200, json_body="<html>")
        context.__aenter__.return_value.headers = {"Content-Type": "text/html"}
        mock_session.get = MagicMock(return_value=context)
        executor.set_session(mock_session)

        with pytest.raises(ApiRequestError):
            await executor.execute_request("musicbrainz", "https://mb.example/ws")

    @pytest.mark.asyncio
    async def test_missing_session_is_a_failure(self, executor: ApiRequestExecutor) -> None:
        """A request that cannot even be prepared fails rather than reading as "not found"."""
        with pytest.raises(ApiRequestError):
            await executor.execute_request("musicbrainz", "https://mb.example/ws")


class TestBuildLogUrl:
    """Tests for _build_log_url static method."""

    def test_build_log_url_no_params(self) -> None:
        """Test building log URL without params."""
        url = ApiRequestExecutor._build_log_url("https://api.example.com", None)
        assert url == "https://api.example.com"

    def test_build_log_url_with_params(self) -> None:
        """Test building log URL with params."""
        url = ApiRequestExecutor._build_log_url(
            "https://api.example.com",
            {"artist": "Test", "album": "Album"},
        )
        assert "artist=Test" in url
        assert "album=Album" in url


class TestPrepareRequest:
    """Tests for _prepare_request method."""

    def test_prepare_request_no_session(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test prepare request fails without session."""
        result = executor._prepare_request("api", "url", None, None)
        assert result is None

    def test_prepare_request_closed_session(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test prepare request fails with closed session."""
        mock_session.closed = True
        executor.set_session(mock_session)

        result = executor._prepare_request("api", "url", None, None)

        assert result is None

    def test_prepare_request_no_rate_limiter(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test prepare request fails without rate limiter."""
        executor.set_session(mock_session)

        result = executor._prepare_request("unknown_api", "url", None, None)

        assert result is None

    def test_prepare_request_discogs_without_token(
        self,
        mock_cache_service: AsyncMock,
        mock_rate_limiters: dict[str, "ApiRateLimiter"],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_session: MagicMock,
    ) -> None:
        """Test Discogs request fails without token."""
        executor = ApiRequestExecutor(
            cache_service=cast("CacheServiceProtocol", cast(object, mock_cache_service)),
            rate_limiters=mock_rate_limiters,
            console_logger=console_logger,
            error_logger=error_logger,
            user_agent="TestAgent/1.0",
            discogs_token=None,
            cache_ttl_seconds=86400,
            default_max_retries=3,
            default_retry_delay=0.01,
        )
        executor.set_session(mock_session)

        result = executor._prepare_request("discogs", "url", None, None)

        assert result is None

    def test_prepare_request_success(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test successful request preparation."""
        executor.set_session(mock_session)

        result = executor._prepare_request("musicbrainz", "url", None, None)

        assert result is not None
        headers, limiter, _timeout = result
        assert isinstance(headers, dict)
        assert limiter is not None

    def test_prepare_request_discogs_adds_auth(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test Discogs request adds authorization header."""
        executor.set_session(mock_session)

        result = executor._prepare_request("discogs", "url", None, None)

        assert result is not None
        headers, _, _ = result
        assert "Authorization" in headers
        assert "Discogs token=test_token" in headers["Authorization"]

    def test_prepare_request_with_headers_override(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test request preparation with header override."""
        executor.set_session(mock_session)

        result = executor._prepare_request("musicbrainz", "url", {"Accept": "application/json"}, None)

        assert result is not None
        headers, _, _ = result
        assert headers["Accept"] == "application/json"

    def test_prepare_request_with_timeout_override(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test request preparation with timeout override."""
        executor.set_session(mock_session)

        result = executor._prepare_request("musicbrainz", "url", None, 60.0)

        assert result is not None
        _, _, timeout = result
        assert timeout.total == 60.0


class TestEnsureSession:
    """Tests for _ensure_session method."""

    def test_ensure_session_raises_when_none(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test raises when session is None."""
        with pytest.raises(RuntimeError, match="session not initialized"):
            executor._ensure_session()

    def test_ensure_session_raises_when_closed(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test raises when session is closed."""
        mock_session.closed = True
        executor.set_session(mock_session)

        with pytest.raises(RuntimeError, match="session not initialized"):
            executor._ensure_session()

    def test_ensure_session_success(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test success when session is valid."""
        executor.set_session(mock_session)

        # Should not raise
        executor._ensure_session()


class TestSessionGuardInRetryLoop:
    """Tests for the defensive session guard after _ensure_session."""

    @pytest.mark.asyncio
    async def test_raises_when_session_none_after_ensure(
        self,
        executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """Guard raises RuntimeError if session is None despite _ensure_session."""
        # Bypass _ensure_session to simulate edge case where session is still None
        with patch.object(executor, "_ensure_session"), pytest.raises(RuntimeError, match="HTTP session lost"):
            await executor._execute_single_request(
                api_name="musicbrainz",
                url="https://api.example.com/test",
                params=None,
                request_headers={"User-Agent": "Test"},
                request_timeout=aiohttp.ClientTimeout(total=30),
                limiter=mock_rate_limiter,
                attempt=0,
                log_url="https://api.example.com/test",
            )


class TestConstants:
    """Tests for module constants."""

    def test_http_constants(self) -> None:
        """Test HTTP status constants."""
        assert HTTP_TOO_MANY_REQUESTS == 429
        assert HTTP_SERVER_ERROR == 500

    def test_api_response_log_limit(self) -> None:
        """Test API response log limit."""
        assert API_RESPONSE_LOG_LIMIT > 0
        assert isinstance(API_RESPONSE_LOG_LIMIT, int)


class TestExecuteRequest:
    """Tests for execute_request method."""

    @pytest.mark.asyncio
    async def test_execute_request_returns_cached(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
        mock_session: MagicMock,
    ) -> None:
        """Test returns cached response without making request."""
        cached_data = {"results": [{"title": "Album"}]}
        mock_cache_service.get_async.return_value = cached_data
        executor.set_session(mock_session)

        result = await executor.execute_request("musicbrainz", "https://api.example.com/search", params={"artist": "Test"})

        assert result == cached_data

    @pytest.mark.asyncio
    async def test_execute_request_no_session(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """A request without a session is a failure, not an empty answer."""
        mock_cache_service.get_async.return_value = None

        with pytest.raises(ApiRequestError):
            await executor.execute_request(
                "musicbrainz",
                "https://api.example.com/search",
            )


class TestMetrics:
    """Tests for metrics tracking."""

    def test_request_counts_increment(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test request counts can be incremented."""
        executor.request_counts["musicbrainz"] += 1
        assert executor.request_counts["musicbrainz"] == 1

    def test_api_call_durations_append(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test API call durations can be appended."""
        executor.api_call_durations["musicbrainz"].append(0.5)
        assert len(executor.api_call_durations["musicbrainz"]) == 1
        assert executor.api_call_durations["musicbrainz"][0] == 0.5


class TestLogResponseDebug:
    """Tests for _log_response_debug method."""

    def test_log_response_debug(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test debug logging doesn't raise."""
        # Should not raise
        executor._log_response_debug("musicbrainz", 200, "response text")


# =============================================================================
# Integration Tests - HTTP Request/Response Flow
# =============================================================================


class TestExecuteWithRetry:
    """Tests for _execute_with_retry method."""

    @pytest.fixture
    def configured_executor(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> ApiRequestExecutor:
        """Create executor with session configured."""
        executor.set_session(mock_session)
        return executor

    @pytest.mark.asyncio
    async def test_execute_with_retry_success_first_attempt(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """Test successful request on first attempt."""
        expected_result = {"data": "value"}

        with patch.object(configured_executor, "_attempt_request", new_callable=AsyncMock) as mock_attempt:
            mock_attempt.return_value = expected_result

            result = await configured_executor._execute_with_retry(
                api_name="musicbrainz",
                url="https://api.example.com",
                params=None,
                request_headers={"User-Agent": "Test"},
                request_timeout=aiohttp.ClientTimeout(total=30),
                limiter=mock_rate_limiter,
                max_retries=3,
                base_delay=0.01,
            )

            assert result == expected_result
            assert mock_attempt.call_count == 1

    @pytest.mark.asyncio
    async def test_execute_with_retry_success_after_retries(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """Test successful request after retries."""
        expected_result = {"data": "value"}

        with patch.object(configured_executor, "_attempt_request", new_callable=AsyncMock) as mock_attempt:
            # First two attempts fail transiently, third succeeds
            mock_attempt.side_effect = [
                TransientRequestError("HTTP 503", status=503),
                TransientRequestError("TimeoutError"),
                expected_result,
            ]

            result = await configured_executor._execute_with_retry(
                api_name="musicbrainz",
                url="https://api.example.com",
                params=None,
                request_headers={"User-Agent": "Test"},
                request_timeout=aiohttp.ClientTimeout(total=30),
                limiter=mock_rate_limiter,
                max_retries=3,
                base_delay=0.01,
            )

            assert result == expected_result
            assert mock_attempt.call_count == 3

    @pytest.mark.asyncio
    async def test_execute_with_retry_all_attempts_fail(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """Exhausting the retry budget on transient failures raises ApiRequestError."""
        with patch.object(configured_executor, "_attempt_request", new_callable=AsyncMock) as mock_attempt:
            mock_attempt.side_effect = TransientRequestError("HTTP 500", status=500)

            with pytest.raises(ApiRequestError) as exc_info:
                await configured_executor._execute_with_retry(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    max_retries=2,
                    base_delay=0.01,
                )

            assert exc_info.value.status == 500
            # max_retries + 1 attempts (0, 1, 2)
            assert mock_attempt.call_count == 3

    @staticmethod
    def _create_mock_response(status: int, json_data: dict[str, Any]) -> MagicMock:
        """Create a mock HTTP response."""
        response = MagicMock()
        response.status = status
        response.ok = 200 <= status < 300
        response.headers = {"Content-Type": "application/json"}
        response.json = AsyncMock(return_value=json_data)
        response.text = AsyncMock(return_value=json.dumps(json_data))
        response.request_info = MagicMock()
        response.history = ()
        return response


class TestAttemptRequest:
    """Tests for _attempt_request exception handling."""

    @pytest.fixture
    def configured_executor(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> ApiRequestExecutor:
        """Create executor with session configured."""
        executor.set_session(mock_session)
        return executor

    @pytest.mark.asyncio
    async def test_attempt_request_runtime_error(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """A closed event loop means the process is shutting down, so the request fails without a retry."""
        with patch.object(configured_executor, "_execute_single_request", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = RuntimeError("Event loop is closed")

            with pytest.raises(ApiRequestError):
                await configured_executor._attempt_request(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    attempt=0,
                    log_url="https://api.example.com",
                )

    @pytest.mark.asyncio
    async def test_attempt_request_timeout_error(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """A timeout is a transient failure."""
        with patch.object(configured_executor, "_execute_single_request", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = TimeoutError()

            with pytest.raises(TransientRequestError):
                await configured_executor._attempt_request(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    attempt=0,
                    log_url="https://api.example.com",
                )

    @pytest.mark.asyncio
    async def test_attempt_request_client_connector_error(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """A refused connection is a transient failure."""
        with patch.object(configured_executor, "_execute_single_request", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = aiohttp.ClientConnectorError(
                connection_key=MagicMock(),
                os_error=OSError("Connection refused"),
            )

            with pytest.raises(TransientRequestError):
                await configured_executor._attempt_request(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    attempt=0,
                    log_url="https://api.example.com",
                )

    @pytest.mark.asyncio
    async def test_attempt_request_unexpected_error(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """An unexpected error fails the request at once."""
        with patch.object(configured_executor, "_execute_single_request", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = ValueError("Unexpected error")

            with pytest.raises(ApiRequestError):
                await configured_executor._attempt_request(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    attempt=0,
                    log_url="https://api.example.com",
                )


class TestExecuteSingleRequest:
    """Tests for _execute_single_request with HTTP mocking."""

    @pytest.fixture
    def mock_response(self) -> MagicMock:
        """Create a mock HTTP response."""
        response = MagicMock()
        response.status = 200
        response.ok = True
        response.headers = {"Content-Type": "application/json"}
        response.json = AsyncMock(return_value={"data": "value"})
        response.text = AsyncMock(return_value='{"data": "value"}')
        response.request_info = MagicMock()
        response.history = ()
        return response

    @pytest.mark.asyncio
    async def test_execute_single_request_success(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
        mock_rate_limiter: AsyncMock,
        mock_response: MagicMock,
    ) -> None:
        """Test successful single request execution."""
        executor.set_session(mock_session)

        # Create async context manager mock
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_response)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.get.return_value = cm

        with patch.object(executor, "_process_response", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = {"data": "value"}

            result = await executor._execute_single_request(
                api_name="musicbrainz",
                url="https://api.example.com",
                params={"query": "test"},
                request_headers={"User-Agent": "Test"},
                request_timeout=aiohttp.ClientTimeout(total=30),
                limiter=mock_rate_limiter,
                attempt=0,
                log_url="https://api.example.com?query=test",
            )

            assert result == {"data": "value"}
            mock_rate_limiter.acquire.assert_called_once()
            mock_rate_limiter.release.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_single_request_rate_limit_wait(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
        mock_response: MagicMock,
    ) -> None:
        """Test rate limiter wait time logging."""
        executor.set_session(mock_session)

        # Create a rate limiter that returns high wait time
        slow_limiter = AsyncMock()
        slow_limiter.acquire = AsyncMock(return_value=WAIT_TIME_LOG_THRESHOLD + 1.0)
        slow_limiter.release = MagicMock()

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_response)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.get.return_value = cm

        with patch.object(executor, "_process_response", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = {"data": "value"}

            result = await executor._execute_single_request(
                api_name="musicbrainz",
                url="https://api.example.com",
                params=None,
                request_headers={"User-Agent": "Test"},
                request_timeout=aiohttp.ClientTimeout(total=30),
                limiter=slow_limiter,
                attempt=0,
                log_url="https://api.example.com",
            )

            assert result is not None
            # Verify request count was incremented
            assert executor.request_counts["musicbrainz"] == 1

    @pytest.mark.asyncio
    async def test_execute_single_request_releases_limiter_on_exception(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """Test rate limiter is released even when request fails."""
        executor.set_session(mock_session)

        # Make session.get raise an exception
        mock_session.get.side_effect = aiohttp.ClientError("Connection failed")

        with pytest.raises(aiohttp.ClientError):
            await executor._execute_single_request(
                api_name="musicbrainz",
                url="https://api.example.com",
                params=None,
                request_headers={"User-Agent": "Test"},
                request_timeout=aiohttp.ClientTimeout(total=30),
                limiter=mock_rate_limiter,
                attempt=0,
                log_url="https://api.example.com",
            )

        # Limiter should still be released in finally block
        mock_rate_limiter.release.assert_called_once()


class TestProcessResponse:
    """Tests for _process_response method."""

    @pytest.fixture
    def mock_response(self) -> MagicMock:
        """Create a base mock HTTP response."""
        response = MagicMock()
        response.status = 200
        response.ok = True
        response.headers = {"Content-Type": "application/json"}
        response.request_info = MagicMock()
        response.request_info.headers = {}
        response.history = ()
        return response

    @pytest.mark.asyncio
    async def test_process_response_success_json(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
    ) -> None:
        """Test processing successful JSON response."""
        mock_response.json = AsyncMock(return_value={"results": []})

        with patch.object(executor, "_read_response_text", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = '{"results": []}'

            with patch.object(executor, "_parse_json_response", new_callable=AsyncMock) as mock_parse:
                mock_parse.return_value = {"results": []}

                result = await executor._process_response(mock_response, api_name="musicbrainz", attempt=0, log_url="log_url", elapsed=0.5)

                assert result == {"results": []}

    @pytest.mark.asyncio
    async def test_process_response_rate_limited(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
    ) -> None:
        """Test processing 429 rate limited response."""
        mock_response.status = HTTP_TOO_MANY_REQUESTS
        mock_response.ok = False

        with patch.object(executor, "_read_response_text", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = "Rate limited"

            with pytest.raises(TransientRequestError) as exc_info:
                await executor._process_response(mock_response, api_name="musicbrainz", attempt=0, log_url="log_url", elapsed=0.5)

            assert exc_info.value.status == HTTP_TOO_MANY_REQUESTS

    @pytest.mark.asyncio
    async def test_process_response_server_error(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
    ) -> None:
        """Test processing 500 server error response."""
        mock_response.status = HTTP_SERVER_ERROR
        mock_response.ok = False

        with patch.object(executor, "_read_response_text", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = "Internal Server Error"

            with pytest.raises(TransientRequestError) as exc_info:
                await executor._process_response(mock_response, api_name="musicbrainz", attempt=0, log_url="log_url", elapsed=0.5)

            assert exc_info.value.status == HTTP_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_process_response_not_ok(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
    ) -> None:
        """A non-transient client error fails the request at once."""
        mock_response.status = 403
        mock_response.ok = False

        with patch.object(executor, "_read_response_text", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = "Forbidden"

            with pytest.raises(ApiRequestError) as exc_info:
                await executor._process_response(mock_response, api_name="musicbrainz", attempt=0, log_url="log_url", elapsed=0.5)

            assert exc_info.value.status == 403

    @pytest.mark.asyncio
    async def test_process_response_non_json_content(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
    ) -> None:
        """Test processing non-JSON response."""
        mock_response.headers = {"Content-Type": "text/html"}

        with patch.object(executor, "_read_response_text", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = "<html>Not JSON</html>"

            with pytest.raises(ApiRequestError):
                await executor._process_response(mock_response, api_name="musicbrainz", attempt=0, log_url="log_url", elapsed=0.5)

    @pytest.mark.asyncio
    async def test_process_response_itunes_text_javascript(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
    ) -> None:
        """Test iTunes API with text/javascript content type."""
        mock_response.headers = {"Content-Type": "text/javascript; charset=utf-8"}

        with patch.object(executor, "_read_response_text", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = '{"resultCount": 1, "results": []}'

            with patch.object(executor, "_parse_json_response", new_callable=AsyncMock) as mock_parse:
                mock_parse.return_value = {"resultCount": 1, "results": []}

                result = await executor._process_response(mock_response, api_name="itunes", attempt=0, log_url="log_url", elapsed=0.5)

                assert result == {"resultCount": 1, "results": []}
                mock_parse.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_response_discogs_logs_headers(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
    ) -> None:
        """Test Discogs logs request headers."""
        mock_response.headers = {"Content-Type": "application/json"}

        with patch.object(executor, "_read_response_text", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = '{"results": []}'

            with patch.object(executor, "_parse_json_response", new_callable=AsyncMock) as mock_parse:
                mock_parse.return_value = {"results": []}

                result = await executor._process_response(mock_response, api_name="discogs", attempt=0, log_url="log_url", elapsed=0.5)
                assert result == {"results": []}

    @pytest.mark.asyncio
    async def test_process_response_discogs_redacts_authorization_header(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Discogs header logging never includes the token from the Authorization header."""
        mock_response.request_info.headers = {
            "Authorization": f"Discogs token={TEST_API_TOKEN}",
            "User-Agent": "TestAgent/1.0",
        }

        with (
            patch.object(executor, "_read_response_text", new_callable=AsyncMock, return_value='{"results": []}'),
            patch.object(executor, "_parse_json_response", new_callable=AsyncMock, return_value={"results": []}),
            caplog.at_level(logging.DEBUG),
        ):
            await executor._process_response(mock_response, api_name="discogs", attempt=0, log_url="log_url", elapsed=0.5)

        assert TEST_API_TOKEN not in caplog.text
        assert "TestAgent/1.0" in caplog.text


class TestReadResponseText:
    """Tests for _read_response_text method."""

    @pytest.mark.asyncio
    async def test_read_response_text_success(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test successful response text reading."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value='{"data": "value"}')

        result = await executor._read_response_text(mock_response, "musicbrainz")

        assert result == '{"data": "value"}'

    @pytest.mark.asyncio
    async def test_read_response_text_truncates_long_content(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test response text is truncated to limit."""
        long_content = "x" * (API_RESPONSE_LOG_LIMIT + 1000)
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=long_content)

        result = await executor._read_response_text(mock_response, "musicbrainz")

        assert len(result) == API_RESPONSE_LOG_LIMIT

    @pytest.mark.asyncio
    async def test_read_response_text_handles_error(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test handles error reading response."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(side_effect=OSError("Read error"))

        result = await executor._read_response_text(mock_response, "musicbrainz")

        assert "[Error Reading Response:" in result

    @pytest.mark.asyncio
    async def test_read_response_text_various_errors(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test handles various error types."""
        error_types = [
            ValueError("Value error"),
            RuntimeError("Runtime error"),
            KeyError("Key error"),
            TypeError("Type error"),
            AttributeError("Attribute error"),
        ]

        for error in error_types:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(side_effect=error)

            result = await executor._read_response_text(mock_response, "musicbrainz")
            assert "[Error Reading Response:" in result


class TestParseJsonResponse:
    """Tests for _parse_json_response method."""

    @pytest.mark.asyncio
    async def test_parse_json_response_success(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test successful JSON parsing."""
        mock_response = MagicMock()
        mock_response.json = AsyncMock(return_value={"results": []})

        result = await executor._parse_json_response(mock_response, "musicbrainz", "url", "snippet")

        assert result == {"results": []}

    @pytest.mark.asyncio
    async def test_parse_json_response_not_dict(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test JSON that is not a dict."""
        mock_response = MagicMock()
        mock_response.json = AsyncMock(return_value=["list", "not", "dict"])

        with pytest.raises(ApiRequestError):
            await executor._parse_json_response(mock_response, "musicbrainz", "url", "snippet")

    @pytest.mark.asyncio
    async def test_parse_json_response_content_type_error(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test ContentTypeError triggers special handling."""
        mock_response = MagicMock()
        mock_response.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(
                request_info=MagicMock(),
                history=(),
                message="Wrong content type",
            )
        )

        with patch.object(executor, "_handle_content_type_error", new_callable=AsyncMock) as mock_handler:
            mock_handler.return_value = {"fallback": "data"}

            result = await executor._parse_json_response(mock_response, "itunes", "url", "snippet")

            assert result == {"fallback": "data"}
            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_json_response_json_decode_error(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test JSONDecodeError handling."""
        mock_response = MagicMock()
        mock_response.json = AsyncMock(side_effect=json.JSONDecodeError("Invalid JSON", "", 0))

        with pytest.raises(ApiRequestError):
            await executor._parse_json_response(mock_response, "musicbrainz", "url", "snippet")


class TestHandleContentTypeError:
    """Tests for _handle_content_type_error method."""

    @pytest.mark.asyncio
    async def test_handle_content_type_error_non_itunes(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """A non-iTunes API with the wrong content type fails the request."""
        mock_response = MagicMock()
        error = aiohttp.ContentTypeError(
            request_info=MagicMock(),
            history=(),
            message="Wrong content type",
        )

        with pytest.raises(ApiRequestError):
            await executor._handle_content_type_error(mock_response, "musicbrainz", "url", "snippet", error)

    @pytest.mark.asyncio
    async def test_handle_content_type_error_itunes_success(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test iTunes manual JSON parsing success."""
        mock_response = MagicMock()
        mock_response.text = AsyncMock(return_value='{"resultCount": 1, "results": [{"trackId": 1}]}')
        error = aiohttp.ContentTypeError(
            request_info=MagicMock(),
            history=(),
            message="Wrong content type",
        )

        result = await executor._handle_content_type_error(mock_response, "itunes", "url", "snippet", error)

        assert result == {"resultCount": 1, "results": [{"trackId": 1}]}

    @pytest.mark.asyncio
    async def test_handle_content_type_error_itunes_not_dict(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test iTunes JSON that is not a dict."""
        mock_response = MagicMock()
        mock_response.text = AsyncMock(return_value='["list", "not", "dict"]')
        error = aiohttp.ContentTypeError(
            request_info=MagicMock(),
            history=(),
            message="Wrong content type",
        )

        with pytest.raises(ApiRequestError):
            await executor._handle_content_type_error(mock_response, "itunes", "url", "snippet", error)

    @pytest.mark.asyncio
    async def test_handle_content_type_error_itunes_invalid_json(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test iTunes invalid JSON handling."""
        mock_response = MagicMock()
        mock_response.text = AsyncMock(return_value="not valid json")
        error = aiohttp.ContentTypeError(
            request_info=MagicMock(),
            history=(),
            message="Wrong content type",
        )

        with pytest.raises(ApiRequestError):
            await executor._handle_content_type_error(mock_response, "itunes", "url", "snippet", error)

    @pytest.mark.asyncio
    async def test_handle_content_type_error_itunes_unicode_error(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test iTunes UnicodeDecodeError handling."""
        mock_response = MagicMock()
        mock_response.text = AsyncMock(side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "Invalid"))
        error = aiohttp.ContentTypeError(
            request_info=MagicMock(),
            history=(),
            message="Wrong content type",
        )

        with pytest.raises(ApiRequestError):
            await executor._handle_content_type_error(mock_response, "itunes", "url", "snippet", error)


class TestExecuteRequestIntegration:
    """Integration tests for execute_request method."""

    @pytest.mark.asyncio
    async def test_execute_request_itunes_debug_logging(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
        mock_session: MagicMock,
    ) -> None:
        """Test iTunes API request enables debug logging."""
        cached_data = {"resultCount": 1, "results": []}
        mock_cache_service.get_async.return_value = cached_data
        executor.set_session(mock_session)

        result = await executor.execute_request("itunes", "https://itunes.apple.com/search", params={"term": "test"})

        assert result == cached_data

    @pytest.mark.asyncio
    async def test_execute_request_with_custom_retries(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
        mock_session: MagicMock,
    ) -> None:
        """Test execute_request with custom retry parameters."""
        mock_cache_service.get_async.return_value = None
        executor.set_session(mock_session)

        # Mock _execute_with_retry
        with patch.object(executor, "_execute_with_retry", new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = {"data": "value"}

            result = await executor.execute_request(
                "musicbrainz",
                "https://api.example.com",
                max_retries=5,
                base_delay=2.0,
            )

            assert result == {"data": "value"}
            # Verify custom retry params were passed
            call_kwargs = mock_retry.call_args[1]
            assert call_kwargs["max_retries"] == 5
            assert call_kwargs["base_delay"] == 2.0

    @pytest.mark.asyncio
    async def test_execute_request_invalid_retry_params_uses_defaults(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
        mock_session: MagicMock,
    ) -> None:
        """Test invalid retry params fall back to defaults."""
        mock_cache_service.get_async.return_value = None
        executor.set_session(mock_session)

        with patch.object(executor, "_execute_with_retry", new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = {"data": "value"}

            # Pass invalid values
            await executor.execute_request(
                "musicbrainz",
                "https://api.example.com",
                max_retries=-1,  # Invalid
                base_delay=-1.0,  # Invalid
            )

            call_kwargs = mock_retry.call_args[1]
            assert call_kwargs["max_retries"] == executor.default_max_retries
            assert call_kwargs["base_delay"] == executor.default_retry_delay

    @pytest.mark.asyncio
    async def test_execute_request_caches_result(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
        mock_session: MagicMock,
    ) -> None:
        """Test result is cached after successful request."""
        mock_cache_service.get_async.return_value = None
        executor.set_session(mock_session)

        with patch.object(executor, "_execute_with_retry", new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = {"data": "value"}

            with patch.object(executor, "_cache_result", new_callable=AsyncMock) as mock_cache:
                await executor.execute_request(
                    "musicbrainz",
                    "https://api.example.com",
                )

                mock_cache.assert_called_once()
                cache_args = mock_cache.call_args[0]
                assert cache_args[1] == {"data": "value"}


class TestWaitTimeLogThreshold:
    """Tests for WAIT_TIME_LOG_THRESHOLD constant."""

    def test_wait_time_threshold_value(self) -> None:
        """Test wait time threshold is reasonable."""
        assert WAIT_TIME_LOG_THRESHOLD > 0
        assert WAIT_TIME_LOG_THRESHOLD <= 10.0  # Should be a few seconds at most
