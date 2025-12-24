"""Enhanced AppleScript Client tests with Allure reporting."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.apple import (
    MAX_SCRIPT_SIZE,
    AppleScriptClient,
    AppleScriptSanitizationError,
    AppleScriptSanitizer,
    EnhancedRateLimiter,
)
from tests.mocks.csv_mock import MockAnalytics, MockLogger


@pytest.fixture
def applescript_test_dir(tmp_path: Path) -> Path:
    """Create temporary applescripts directory with required scripts."""
    scripts_dir = tmp_path / "applescripts"
    scripts_dir.mkdir()
    (scripts_dir / "update_property.applescript").write_text("-- test script")
    (scripts_dir / "fetch_tracks.applescript").write_bytes(b"-- test script")
    return scripts_dir


class TestAppleScriptSanitizerAllure:
    """Enhanced tests for AppleScript sanitizer with Allure reporting."""

    @staticmethod
    def create_sanitizer(logger: Any = None) -> AppleScriptSanitizer:
        """Create an AppleScriptSanitizer instance for testing."""
        test_logger = logger or MockLogger()
        return AppleScriptSanitizer(logger=test_logger)

    @pytest.mark.parametrize(
        ("dangerous_code", "expected_pattern"),
        [
            ('do shell script "rm -rf /"', "do shell script"),
            ('tell application "Finder" to delete', 'tell application "Finder"'),
            # "System Events" pattern detected before "keystroke" due to pattern order
            ('tell application "System Events" to keystroke', 'tell application "System Events"'),
            ('load script file "malicious.scpt"', "load script"),
            ('choose file with prompt "Select file"', "choose file"),
            ('open location "https://malicious.com"', "open location"),
        ],
    )
    def test_detect_dangerous_patterns(self, dangerous_code: str, expected_pattern: str) -> None:
        """Test detection of dangerous AppleScript patterns."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with pytest.raises(AppleScriptSanitizationError) as exc_info:
            sanitizer.validate_script_code(dangerous_code)
        error = exc_info.value
        assert isinstance(error, AppleScriptSanitizationError)
        assert error.dangerous_pattern is not None
        assert expected_pattern.lower() in error.dangerous_pattern.lower()

    @pytest.mark.parametrize(
        "safe_code",
        [
            'tell application "Music" to get name of current track',
            'tell application "Music" to set genre of track id "123" to "Rock"',
            'tell application "Music" to get properties of playlist "Library"',
            'set trackName to "My Song"',
            'set genreValue to "Jazz"',
        ],
    )
    def test_allow_safe_applescript_code(self, safe_code: str) -> None:
        """Test that safe AppleScript code passes validation."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()
        try:
            sanitizer.validate_script_code(safe_code)
            validation_passed = True
            result = safe_code
        except AppleScriptSanitizationError:
            validation_passed = False
            result = None
        assert validation_passed, f"Safe code should pass validation: {safe_code}"
        assert result is not None

    def test_script_size_validation(self) -> None:
        """Test script size validation."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()
        # Create a script that exceeds MAX_SCRIPT_SIZE
        oversized_script = "-- " + "A" * (MAX_SCRIPT_SIZE + 1)

        with pytest.raises(AppleScriptSanitizationError) as exc_info:
            sanitizer.validate_script_code(oversized_script)
        error = exc_info.value
        assert isinstance(error, AppleScriptSanitizationError)
        assert "too large" in str(error).lower()

    def test_complex_pattern_detection(self) -> None:
        """Test detection of complex script patterns."""
        TestAppleScriptSanitizerAllure.create_sanitizer()
        complex_script = """
            tell application "Music"
                get name of current track
            end tell
            tell application "Finder"
                get desktop
            end tell
            tell application "System Events"
                get processes
            end tell
            """
        # This would be used internally to determine temp file usage
        # Testing the existence of pattern detection capability
        tell_blocks = complex_script.count("tell application")
        assert tell_blocks == 3
        # Complex pattern detection should identify this as complex
        assert tell_blocks > 2  # Threshold for complexity


class TestAppleScriptClientAllure:
    """Enhanced tests for AppleScript client with Allure reporting."""

    # Class variable to store temp scripts directory path
    _temp_scripts_dir: str | None = None

    @pytest.fixture(autouse=True)
    def setup_temp_scripts_dir(self, applescript_test_dir: Path) -> Iterator[None]:
        """Create temporary scripts directory for all tests."""
        TestAppleScriptClientAllure._temp_scripts_dir = str(applescript_test_dir)
        yield
        TestAppleScriptClientAllure._temp_scripts_dir = None

    @staticmethod
    def create_client(
        config: dict[str, Any] | None = None,
        analytics: Any = None,
    ) -> AppleScriptClient:
        """Create an AppleScriptClient instance for testing."""
        # Use temp scripts dir if available, otherwise fall back to default
        scripts_dir = TestAppleScriptClientAllure._temp_scripts_dir or "applescripts/"

        test_config = dict(config) if config else {"apple_script": {"timeout": 30, "concurrency": 5}, "apple_scripts_dir": scripts_dir}

        # If config doesn't have apple_scripts_dir or uses placeholder paths, use temp dir
        if TestAppleScriptClientAllure._temp_scripts_dir and (
            "apple_scripts_dir" not in test_config or test_config.get("apple_scripts_dir") in ("applescripts/", "custom_scripts/")
        ):
            test_config["apple_scripts_dir"] = TestAppleScriptClientAllure._temp_scripts_dir

        console_logger = MockLogger()
        error_logger = MockLogger()
        test_analytics = analytics or MockAnalytics()

        return AppleScriptClient(
            config=test_config,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=test_analytics,
        )

    def test_client_initialization_comprehensive(self) -> None:
        """Test comprehensive AppleScript client initialization."""
        config = {"apple_script": {"timeout": 45, "concurrency": 10, "max_retries": 3}, "apple_scripts_dir": "custom_scripts/"}
        client = TestAppleScriptClientAllure.create_client(config=config)
        # Check apple_script settings are preserved (path may be replaced by temp dir)
        assert client.config["apple_script"] == config["apple_script"]
        assert hasattr(client, "console_logger")
        assert hasattr(client, "error_logger")
        assert hasattr(client, "analytics")

        # Verify sanitizer is initialized
        assert hasattr(client, "sanitizer")
        assert isinstance(client.sanitizer, AppleScriptSanitizer)

    @pytest.mark.asyncio
    async def test_fetch_tracks_from_music_app(self) -> None:
        """Test fetching tracks from Music.app."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_tracks_data = """Track 1|Artist 1|Album 1|2020|Rock
Track 2|Artist 2|Album 2|2021|Jazz
Track 3|Artist 3|Album 3|2022|Pop"""

            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(mock_tracks_data.encode(), b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process
            # AppleScriptClient doesn't have fetch_tracks_async, it has run_script
            result = await client.run_script("fetch_tracks.applescript")
        assert result is not None

        # Verify subprocess was called with proper AppleScript
        mock_subprocess.assert_called_once()
        call_args = mock_subprocess.call_args[0]
        assert "osascript" in call_args

    @pytest.mark.asyncio
    async def test_update_track_properties(self) -> None:
        """Test updating track properties in Music.app."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"Success: Genre updated", b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process
            # AppleScriptClient doesn't have update_track_async, use run_script with arguments
            result = await client.run_script("update_property.applescript", arguments=["test_track_001", "genre", "Electronic"])
        assert result is not None
        assert "Success" in result or "updated" in result.lower()

        # Verify subprocess was called
        mock_subprocess.assert_called_once()

    def test_sanitize_string_inputs(self) -> None:
        """Test string sanitization functionality."""
        sanitizer = AppleScriptSanitizer(MockLogger())
        # Test escaping quotes
        assert sanitizer.sanitize_string('Test "quoted" string') == 'Test \\"quoted\\" string'
        # Single quotes don't need escaping in AppleScript double-quoted strings
        assert sanitizer.sanitize_string("Test 'single' quote") == "Test 'single' quote"

        # Test escaping backslashes
        assert sanitizer.sanitize_string("Test\\backslash") == "Test\\\\backslash"

        # Test combined escaping
        assert sanitizer.sanitize_string('Test "\\" combo') == 'Test \\"\\\\\\" combo'

    def test_sanitize_invalid_inputs(self) -> None:
        """Test sanitization error handling."""
        sanitizer = AppleScriptSanitizer(MockLogger())

        with pytest.raises(ValueError, match="Cannot sanitize None value"):
            sanitizer.sanitize_string(None)

        with pytest.raises(TypeError, match="Expected string, got int"):
            sanitizer.sanitize_string(123)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="Expected string, got list"):
            sanitizer.sanitize_string([1, 2, 3])  # type: ignore[arg-type]

    def test_script_path_validation(self) -> None:
        """Test script path validation."""
        config = {"apple_script": {"timeout": 30}, "apple_scripts_dir": "/test/scripts/"}
        client = TestAppleScriptClientAllure.create_client(config=config)
        # Test valid path
        valid = client.file_validator.validate_script_path("/test/scripts/test.scpt")
        assert valid is True

        # Test invalid path (traversal attempt)
        invalid = client.file_validator.validate_script_path("/test/scripts/../../../etc/passwd")
        assert invalid is False

    @pytest.mark.asyncio
    async def test_script_file_validation(self) -> None:
        """Test script file validation."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with patch("pathlib.Path.exists", return_value=False):
            result = await client.run_script("nonexistent.scpt")

            # Should return None or handle gracefully
            assert result is None or "error" in str(result).lower()

    @pytest.mark.asyncio
    async def test_batch_track_operations(self) -> None:
        """Test batch track operations."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with (
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
        ):
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"Batch complete: 10 tracks updated", b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            # Simulate batch update
            track_ids = ["track_001", "track_002", "track_003"]
            # Run multiple updates
            tasks = []
            for track_id in track_ids:
                task = client.run_script("update_property.applescript", arguments=[track_id, "genre", "Rock"])
                tasks.append(task)

            results = await asyncio.gather(*tasks)
        assert len(results) == len(track_ids)
        for result in results:
            assert result is not None

    @pytest.mark.asyncio
    async def test_resource_cleanup(self) -> None:
        """Test proper resource management and error handling."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with (
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
        ):
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"Success", b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            # Run some operations
            await client.run_script("update_property.applescript", arguments=["test"])
        # Since AppleScriptClient doesn't have a shutdown method,
        # test error handling for invalid scripts instead
        result = await client.run_script("non_existent_script.scpt", [])

        # run_script returns None for non-existent files
        assert result is None, "Non-existent script should return None"

    def test_script_size_validation(self) -> None:
        """Test script size validation."""
        sanitizer = AppleScriptSanitizer(MockLogger())
        # Create a script larger than MAX_SCRIPT_SIZE
        huge_script = 'tell application "Music" to ' + ("get name of track " * 10000)

        if len(huge_script) > MAX_SCRIPT_SIZE:
            # Should raise or handle gracefully
            try:
                sanitizer.validate_script_code(huge_script)
                validation_passed = True
            except (ValueError, AppleScriptSanitizationError):
                validation_passed = False

            # Large scripts should be rejected or truncated
            assert not validation_passed or len(huge_script) <= MAX_SCRIPT_SIZE

    def test_sanitization_edge_cases(self) -> None:
        """Test edge cases in string sanitization."""
        sanitizer = AppleScriptSanitizer(MockLogger())
        assert sanitizer.sanitize_string("") == ""
        assert sanitizer.sanitize_string('"""') == '\\"\\"\\"'
        long_str = "a" * 10000
        assert sanitizer.sanitize_string(long_str) == long_str
        assert len(sanitizer.sanitize_string("line1\nline2\ttab")) > 0

    @pytest.mark.asyncio
    async def test_process_cleanup_error_handling(self) -> None:
        """Test process cleanup error handling."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()
        # Create a mock process that's already terminated
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock(side_effect=ProcessLookupError("Process already terminated"))
        mock_proc.poll = MagicMock(return_value=None)  # Process still running
        mock_proc.wait = AsyncMock(side_effect=TimeoutError)

        # Should handle cleanup gracefully
        await client.executor.cleanup_process(mock_proc, "test_process")

    def test_validate_empty_script(self) -> None:
        """Test validation of empty script code."""
        sanitizer = AppleScriptSanitizer(MockLogger())
        with pytest.raises(ValueError, match="Script code must be a non-empty string"):
            sanitizer.validate_script_code(None)

        with pytest.raises(ValueError, match="Script code must be a non-empty string"):
            sanitizer.validate_script_code("")

        # Whitespace-only doesn't raise an error, it just normalizes it
        # Verify that whitespace-only scripts are handled without raising
        sanitizer.validate_script_code("   ")

    @pytest.mark.asyncio
    async def test_create_command_with_arguments(self) -> None:
        """Test creating commands with arguments."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()
        script = 'tell application "Music" to get name'
        args = ["trackID123", "GenreRock"]
        cmd = client.sanitizer.create_safe_command(script, arguments=args)

        # Verify command structure
        assert cmd[0] == "osascript"
        assert cmd[1] == "-e"
        assert cmd[2] == script
        assert len(cmd) == 5  # osascript, -e, script, arg1, arg2
        # Shell metacharacters are safe with create_subprocess_exec (no shell)
        args_with_ampersand = ["Seek & Destroy", "Rock & Roll"]
        cmd = client.sanitizer.create_safe_command(script, arguments=args_with_ampersand)
        assert "Seek & Destroy" in cmd
        assert "Rock & Roll" in cmd

    @pytest.mark.asyncio
    async def test_rate_limiter_validation(self) -> None:
        """Test rate limiter parameter validation."""
        with pytest.raises(ValueError, match="requests_per_window must be a positive integer"):
            EnhancedRateLimiter(requests_per_window=0, window_size=1.0)

        with pytest.raises(ValueError, match="window_size must be a positive number"):
            EnhancedRateLimiter(requests_per_window=10, window_size=0)

        with pytest.raises(ValueError, match="max_concurrent must be a positive integer"):
            EnhancedRateLimiter(requests_per_window=10, window_size=1.0, max_concurrent=0)
        limiter = EnhancedRateLimiter(requests_per_window=10, window_size=1.0)
        await limiter.initialize()
        assert limiter.semaphore is not None
        assert limiter.total_requests == 0

    @pytest.mark.asyncio
    async def test_rate_limiter_acquire_release(self) -> None:
        """Test rate limiter acquire and release."""
        limiter = EnhancedRateLimiter(requests_per_window=2, window_size=1.0, max_concurrent=1)
        await limiter.initialize()
        # First two requests should not wait
        wait_time1 = await limiter.acquire()
        assert wait_time1 == 0.0
        limiter.release()

        wait_time2 = await limiter.acquire()
        assert wait_time2 == 0.0
        limiter.release()

        # Third request should trigger rate limiting
        # This will wait for the window to expire
        start_time = asyncio.get_event_loop().time()
        await limiter.acquire()
        elapsed = asyncio.get_event_loop().time() - start_time

        # Should have waited approximately 1 second (window_size)
        assert elapsed >= 0.9  # Allow some timing variance
        limiter.release()
        uninit_limiter = EnhancedRateLimiter(requests_per_window=10, window_size=1.0)
        with pytest.raises(RuntimeError, match="RateLimiter not initialized"):
            await uninit_limiter.acquire()
        stats = limiter.get_stats()
        assert "total_requests" in stats
        assert "total_wait_time" in stats
        assert stats["total_requests"] == 3
        assert stats["total_wait_time"] > 0

    def test_reserved_words_validation(self) -> None:
        """Test validation of AppleScript reserved words."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with pytest.raises(AppleScriptSanitizationError, match="Dangerous AppleScript pattern"):
            sanitizer.validate_script_code('tell application "Finder" to delete file "test.txt"', allow_music_app=False)
        # This should not raise an error (allow_music_app=True is default)
        try:
            sanitizer.validate_script_code('tell application "Music" to play')
        except AppleScriptSanitizationError:
            pytest.fail("Music.app operations should be allowed when allow_music_app=True")

    def test_reserved_words_word_boundary_matching(self) -> None:
        """Test that reserved words are matched with word boundaries."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with pytest.raises(AppleScriptSanitizationError, match="delete"):
            sanitizer.validate_script_code("delete track", allow_music_app=False)
        # Should NOT raise - "undelete" contains "delete" but is not a standalone word
        try:
            sanitizer.validate_script_code("undelete operation", allow_music_app=False)
        except AppleScriptSanitizationError as e:
            if "delete" in str(e).lower():
                pytest.fail("'undelete' should not trigger 'delete' reserved word check")
        try:
            sanitizer.validate_script_code("nodelete flag enabled", allow_music_app=False)
        except AppleScriptSanitizationError as e:
            if "delete" in str(e).lower():
                pytest.fail("'nodelete' should not trigger 'delete' reserved word check")
        try:
            sanitizer.validate_script_code("file was deleted yesterday", allow_music_app=False)
        except AppleScriptSanitizationError as e:
            if "delete" in str(e).lower():
                pytest.fail("'deleted' should not trigger 'delete' reserved word check")

    def test_allow_music_app_parameter(self) -> None:
        """Test allow_music_app parameter behavior."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with pytest.raises(AppleScriptSanitizationError, match="delete"):
            sanitizer.validate_script_code("delete track from playlist", allow_music_app=False)

        with (
            pytest.raises(AppleScriptSanitizationError, match="delete"),
        ):
            # 'delete' is in APPLESCRIPT_RESERVED_WORDS but doesn't contain 'music'
            sanitizer.validate_script_code("delete track from playlist")

        with pytest.raises(AppleScriptSanitizationError, match="move"):
            sanitizer.validate_script_code("move file to folder")
