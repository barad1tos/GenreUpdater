"""Tests for ApiRequestExecutor - HTTP request execution with retry and caching."""

import json
import logging
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from services.api.request_executor import (
    API_RESPONSE_LOG_LIMIT,
    HTTP_SERVER_ERROR,
    HTTP_TOO_MANY_REQUESTS,
    WAIT_TIME_LOG_THRESHOLD,
    ApiRequestExecutor,
)

if TYPE_CHECKING:
    from core.models.protocols import CacheServiceProtocol
    from services.api.api_base import EnhancedRateLimiter

# Test API token (not a real credential)
TEST_API_TOKEN = "test_token"  # noqa: S105


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create a test console logger."""
    return logging.getLogger("test.api.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create a test error logger."""
    return logging.getLogger("test.api.error")


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
def mock_rate_limiters(mock_rate_limiter: AsyncMock) -> dict[str, "EnhancedRateLimiter"]:
    """Create dict of mock rate limiters for all APIs."""
    return cast(
        dict[str, "EnhancedRateLimiter"],
        {
            "discogs": mock_rate_limiter,
            "musicbrainz": mock_rate_limiter,
            "itunes": mock_rate_limiter,
        },
    )


@pytest.fixture
def executor(
    mock_cache_service: AsyncMock,
    mock_rate_limiters: dict[str, "EnhancedRateLimiter"],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> ApiRequestExecutor:
    """Create an ApiRequestExecutor instance."""
    return ApiRequestExecutor(
        cache_service=cast("CacheServiceProtocol", mock_cache_service),
        rate_limiters=mock_rate_limiters,
        console_logger=console_logger,
        error_logger=error_logger,
        user_agent="TestAgent/1.0",
        discogs_token=TEST_API_TOKEN,
        cache_ttl_days=1,
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
        mock_rate_limiters: dict[str, "EnhancedRateLimiter"],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test initialization with all parameters."""
        executor = ApiRequestExecutor(
            cache_service=cast("CacheServiceProtocol", mock_cache_service),
            rate_limiters=mock_rate_limiters,
            console_logger=console_logger,
            error_logger=error_logger,
            user_agent="TestAgent/1.0",
            discogs_token=TEST_API_TOKEN,
            cache_ttl_days=7,
            default_max_retries=5,
            default_retry_delay=1.0,
        )

        assert executor.user_agent == "TestAgent/1.0"
        assert executor.discogs_token == TEST_API_TOKEN
        assert executor.cache_ttl_days == 7
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
        assert "api_request" in key
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
    async def test_check_cache_empty_dict(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test cached empty dict is returned."""
        mock_cache_service.get_async.return_value = {}

        result = await executor._check_cache("key", "api", "url")

        assert result == {}

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
    async def test_cache_result_none_as_empty_dict(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test caching None stores empty dict."""
        await executor._cache_result("key", None)

        args = mock_cache_service.set_async.call_args
        assert args[0][1] == {}


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
        mock_rate_limiters: dict[str, "EnhancedRateLimiter"],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_session: MagicMock,
    ) -> None:
        """Test Discogs request fails without token."""
        executor = ApiRequestExecutor(
            cache_service=cast("CacheServiceProtocol", mock_cache_service),
            rate_limiters=mock_rate_limiters,
            console_logger=console_logger,
            error_logger=error_logger,
            user_agent="TestAgent/1.0",
            discogs_token=None,
            cache_ttl_days=1,
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


class TestHandleClientError:
    """Tests for _handle_client_error method."""

    @pytest.mark.asyncio
    async def test_handle_client_error_max_retries_exceeded(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test handling when max retries exceeded."""
        error = aiohttp.ClientConnectorError(
            connection_key=MagicMock(),
            os_error=OSError("Connection refused"),
        )

        result = await executor._handle_client_error(
            error,
            "musicbrainz",
            attempt=3,
            max_retries=3,
            base_delay=0.01,
            url="https://api.example.com",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_handle_client_error_non_retryable(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test handling non-retryable error."""
        # ValueError is not in retryable_errors
        error = aiohttp.ClientPayloadError("Payload error")

        result = await executor._handle_client_error(
            error,
            "musicbrainz",
            attempt=0,
            max_retries=3,
            base_delay=0.01,
            url="https://api.example.com",
        )

        assert result is None


class TestHandleUnexpectedError:
    """Tests for _handle_unexpected_error method."""

    def test_handle_unexpected_error_logs(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test unexpected error is logged."""
        error = ValueError("Unexpected error")

        # Should not raise
        executor._handle_unexpected_error(error, "musicbrainz", "https://api.example.com")


class TestLogFinalFailure:
    """Tests for _log_final_failure method."""

    def test_log_final_failure(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test final failure is logged."""
        error = Exception("Test error")

        # Should not raise
        executor._log_final_failure("musicbrainz", "https://api.example.com", error)


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

        result = await executor.execute_request(
            "musicbrainz",
            "https://api.example.com/search",
            {"artist": "Test"},
        )

        assert result == cached_data

    @pytest.mark.asyncio
    async def test_execute_request_no_session(
        self,
        executor: ApiRequestExecutor,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test returns None when no session."""
        mock_cache_service.get_async.return_value = None

        result = await executor.execute_request(
            "musicbrainz",
            "https://api.example.com/search",
        )

        assert result is None


class TestCreateResponseError:
    """Tests for _create_response_error static method."""

    def test_create_response_error(self) -> None:
        """Test creating response error."""
        mock_response = MagicMock()
        mock_response.request_info = MagicMock()
        mock_response.history = ()

        error = ApiRequestExecutor._create_response_error(
            mock_response,
            status=404,
            message="Not Found",
        )

        assert isinstance(error, aiohttp.ClientResponseError)
        assert error.status == 404
        assert error.message == "Not Found"


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
            # First two attempts fail, third succeeds
            mock_attempt.side_effect = [None, None, expected_result]

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
        """Test all retry attempts fail."""
        with patch.object(configured_executor, "_attempt_request", new_callable=AsyncMock) as mock_attempt:
            mock_attempt.return_value = None

            result = await configured_executor._execute_with_retry(
                api_name="musicbrainz",
                url="https://api.example.com",
                params=None,
                request_headers={"User-Agent": "Test"},
                request_timeout=aiohttp.ClientTimeout(total=30),
                limiter=mock_rate_limiter,
                max_retries=2,
                base_delay=0.01,
            )

            assert result is None
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
        """Test handling RuntimeError in attempt."""
        with patch.object(configured_executor, "_execute_single_request", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = RuntimeError("Event loop is closed")

            with patch.object(configured_executor, "_handle_runtime_error", new_callable=AsyncMock) as mock_handler:
                mock_handler.return_value = None

                result = await configured_executor._attempt_request(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    attempt=0,
                    log_url="https://api.example.com",
                    max_retries=3,
                    base_delay=0.01,
                )

                assert result is None
                mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_attempt_request_timeout_error(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """Test handling TimeoutError in attempt."""
        with patch.object(configured_executor, "_execute_single_request", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = TimeoutError()

            with patch.object(configured_executor, "_handle_client_error", new_callable=AsyncMock) as mock_handler:
                mock_handler.return_value = None

                result = await configured_executor._attempt_request(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    attempt=0,
                    log_url="https://api.example.com",
                    max_retries=3,
                    base_delay=0.01,
                )

                assert result is None
                mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_attempt_request_client_connector_error(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """Test handling ClientConnectorError in attempt."""
        with patch.object(configured_executor, "_execute_single_request", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = aiohttp.ClientConnectorError(
                connection_key=MagicMock(),
                os_error=OSError("Connection refused"),
            )

            with patch.object(configured_executor, "_handle_client_error", new_callable=AsyncMock) as mock_handler:
                mock_handler.return_value = None

                result = await configured_executor._attempt_request(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    attempt=0,
                    log_url="https://api.example.com",
                    max_retries=3,
                    base_delay=0.01,
                )

                assert result is None
                mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_attempt_request_unexpected_error(
        self,
        configured_executor: ApiRequestExecutor,
        mock_rate_limiter: AsyncMock,
    ) -> None:
        """Test handling unexpected errors in attempt."""
        with patch.object(configured_executor, "_execute_single_request", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = ValueError("Unexpected error")

            with patch.object(configured_executor, "_handle_unexpected_error") as mock_handler:
                result = await configured_executor._attempt_request(
                    api_name="musicbrainz",
                    url="https://api.example.com",
                    params=None,
                    request_headers={"User-Agent": "Test"},
                    request_timeout=aiohttp.ClientTimeout(total=30),
                    limiter=mock_rate_limiter,
                    attempt=0,
                    log_url="https://api.example.com",
                    max_retries=3,
                    base_delay=0.01,
                )

                assert result is None
                mock_handler.assert_called_once()


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

                result = await executor._process_response(mock_response, "musicbrainz", "url", 0, "log_url", 0.5)

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

            with pytest.raises(aiohttp.ClientResponseError) as exc_info:
                await executor._process_response(mock_response, "musicbrainz", "url", 0, "log_url", 0.5)

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

            with pytest.raises(aiohttp.ClientResponseError) as exc_info:
                await executor._process_response(mock_response, "musicbrainz", "url", 0, "log_url", 0.5)

            assert exc_info.value.status == HTTP_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_process_response_not_ok(
        self,
        executor: ApiRequestExecutor,
        mock_response: MagicMock,
    ) -> None:
        """Test processing non-OK response."""
        mock_response.status = 404
        mock_response.ok = False

        with patch.object(executor, "_read_response_text", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = "Not Found"

            with pytest.raises(aiohttp.ClientResponseError) as exc_info:
                await executor._process_response(mock_response, "musicbrainz", "url", 0, "log_url", 0.5)

            assert exc_info.value.status == 404

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

            result = await executor._process_response(mock_response, "musicbrainz", "url", 0, "log_url", 0.5)

            assert result is None

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

                result = await executor._process_response(mock_response, "itunes", "url", 0, "log_url", 0.5)

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

                # Should not raise
                await executor._process_response(mock_response, "discogs", "url", 0, "log_url", 0.5)


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


class TestHandleRuntimeError:
    """Tests for _handle_runtime_error method."""

    @pytest.mark.asyncio
    async def test_handle_runtime_error_event_loop_closed_retryable(
        self,
        executor: ApiRequestExecutor,
        mock_session: MagicMock,
    ) -> None:
        """Test event loop closed error with retries remaining."""
        executor.set_session(mock_session)
        error = RuntimeError("Event loop is closed")

        result = await executor._handle_runtime_error(error, "musicbrainz", attempt=0, max_retries=3, url="https://api.example.com")

        assert result is None
        assert executor.session is None  # Session should be cleared

    @pytest.mark.asyncio
    async def test_handle_runtime_error_event_loop_closed_max_retries(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test event loop closed error at max retries."""
        error = RuntimeError("Event loop is closed")

        result = await executor._handle_runtime_error(error, "musicbrainz", attempt=3, max_retries=3, url="https://api.example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_handle_runtime_error_other_error(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test other RuntimeError types."""
        error = RuntimeError("Some other runtime error")

        result = await executor._handle_runtime_error(error, "musicbrainz", attempt=0, max_retries=3, url="https://api.example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_handle_runtime_error_session_close_fails(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test handles failure to close session."""
        # Create a session that fails to close
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock(side_effect=aiohttp.ClientError("Close failed"))
        executor.set_session(mock_session)

        error = RuntimeError("Event loop is closed")

        # Should not raise, handles close failure gracefully
        result = await executor._handle_runtime_error(error, "musicbrainz", attempt=0, max_retries=3, url="https://api.example.com")

        assert result is None
        assert executor.session is None


class TestHandleClientErrorIntegration:
    """Integration tests for _handle_client_error with retries."""

    @pytest.mark.asyncio
    async def test_handle_client_error_connector_error_retryable(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test connector error triggers retry with delay."""
        error = aiohttp.ClientConnectorError(
            connection_key=MagicMock(),
            os_error=OSError("Connection refused"),
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await executor._handle_client_error(
                error,
                "musicbrainz",
                attempt=0,
                max_retries=3,
                base_delay=0.01,
                url="https://api.example.com",
            )

            assert result is None
            mock_sleep.assert_called_once()
            # Verify delay is calculated with jitter
            delay = mock_sleep.call_args[0][0]
            assert delay >= 0.008  # 0.01 * 0.8
            assert delay <= 0.014  # 0.01 * 1.4

    @pytest.mark.asyncio
    async def test_handle_client_error_server_disconnected_retryable(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test ServerDisconnectedError triggers retry."""
        error = aiohttp.ServerDisconnectedError("Server disconnected")

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await executor._handle_client_error(
                error,
                "musicbrainz",
                attempt=1,
                max_retries=3,
                base_delay=0.01,
                url="https://api.example.com",
            )

            assert result is None
            mock_sleep.assert_called_once()
            # Second attempt: delay = 0.01 * 2^1 * jitter = ~0.02
            delay = mock_sleep.call_args[0][0]
            assert delay >= 0.016  # 0.02 * 0.8
            assert delay <= 0.028  # 0.02 * 1.4

    @pytest.mark.asyncio
    async def test_handle_client_error_timeout_retryable(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test TimeoutError triggers retry."""
        error = TimeoutError()

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await executor._handle_client_error(
                error,
                "musicbrainz",
                attempt=0,
                max_retries=3,
                base_delay=0.01,
                url="https://api.example.com",
            )

            assert result is None
            mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_client_error_tracks_duration(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test failed request duration is tracked."""
        error = aiohttp.ClientConnectorError(
            connection_key=MagicMock(),
            os_error=OSError("Connection refused"),
        )

        initial_count = len(executor.api_call_durations["musicbrainz"])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await executor._handle_client_error(
                error,
                "musicbrainz",
                attempt=0,
                max_retries=3,
                base_delay=0.01,
                url="https://api.example.com",
            )

        # Duration 0.0 should be appended for failed requests
        assert len(executor.api_call_durations["musicbrainz"]) == initial_count + 1
        assert executor.api_call_durations["musicbrainz"][-1] == 0.0


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

        result = await executor._parse_json_response(mock_response, "musicbrainz", "url", "snippet")

        assert result is None

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

        result = await executor._parse_json_response(mock_response, "musicbrainz", "url", "snippet")

        assert result is None


class TestHandleContentTypeError:
    """Tests for _handle_content_type_error method."""

    @pytest.mark.asyncio
    async def test_handle_content_type_error_non_itunes(
        self,
        executor: ApiRequestExecutor,
    ) -> None:
        """Test non-iTunes API returns None."""
        mock_response = MagicMock()
        error = aiohttp.ContentTypeError(
            request_info=MagicMock(),
            history=(),
            message="Wrong content type",
        )

        result = await executor._handle_content_type_error(mock_response, "musicbrainz", "url", "snippet", error)

        assert result is None

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

        result = await executor._handle_content_type_error(mock_response, "itunes", "url", "snippet", error)

        assert result is None

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

        result = await executor._handle_content_type_error(mock_response, "itunes", "url", "snippet", error)

        assert result is None

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

        result = await executor._handle_content_type_error(mock_response, "itunes", "url", "snippet", error)

        assert result is None


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

        result = await executor.execute_request(
            "itunes",
            "https://itunes.apple.com/search",
            {"term": "test"},
        )

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
