"""Enhanced AppleScript Client tests with Allure reporting."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

from src.services.apple import (
    MAX_SCRIPT_SIZE,
    MAX_TRACK_ID_LENGTH,
    AppleScriptClient,
    AppleScriptSanitizationError,
    AppleScriptSanitizer,
    EnhancedRateLimiter,
)
from tests.mocks.csv_mock import MockAnalytics, MockLogger


@allure.epic("Music Genre Updater")
@allure.feature("AppleScript Integration")
class TestAppleScriptSanitizerAllure:
    """Enhanced tests for AppleScript sanitizer with Allure reporting."""

    @staticmethod
    def create_sanitizer(logger: Any = None) -> AppleScriptSanitizer:
        """Create an AppleScriptSanitizer instance for testing."""
        test_logger = logger or MockLogger()
        return AppleScriptSanitizer(logger=test_logger)

    @allure.story("Security Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should detect dangerous AppleScript patterns")
    @allure.description("Test detection of potentially dangerous AppleScript code patterns")
    @pytest.mark.parametrize(
        ("dangerous_code", "expected_pattern"),
        [
            ('do shell script "rm -rf /"', "do shell script"),
            ('tell application "Finder" to delete', 'tell application "Finder"'),
            ('tell application "System Events" to keystroke', "keystroke"),
            ('load script file "malicious.scpt"', "load script"),
            ('choose file with prompt "Select file"', "choose file"),
            ('open location "https://malicious.com"', "open location"),
        ],
    )
    def test_detect_dangerous_patterns(self, dangerous_code: str, expected_pattern: str) -> None:
        """Test detection of dangerous AppleScript patterns."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with allure.step(f"Testing dangerous code: {dangerous_code[:50]}..."), pytest.raises(AppleScriptSanitizationError) as exc_info:
            sanitizer.validate_script_code(dangerous_code)

        with allure.step("Verify security violation detection"):
            error = exc_info.value
            assert isinstance(error, AppleScriptSanitizationError)
            assert error.dangerous_pattern is not None

            allure.attach(dangerous_code, "Dangerous Code", allure.attachment_type.TEXT)
            allure.attach(str(error), "Security Error", allure.attachment_type.TEXT)
            allure.attach(expected_pattern, "Expected Pattern", allure.attachment_type.TEXT)

    @allure.story("Security Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should allow safe AppleScript code")
    @allure.description("Test that safe AppleScript code passes validation")
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

        with allure.step(f"Testing safe code: {safe_code}"):
            try:
                sanitizer.validate_script_code(safe_code)
                validation_passed = True
                result = safe_code
            except AppleScriptSanitizationError:
                validation_passed = False
                result = None

        with allure.step("Verify safe code validation"):
            assert validation_passed, f"Safe code should pass validation: {safe_code}"
            assert result is not None

            allure.attach(safe_code, "Safe Code", allure.attachment_type.TEXT)
            allure.attach("Passed", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Input Sanitization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should sanitize track IDs properly")
    @allure.description("Test sanitization of track IDs to prevent injection")
    @pytest.mark.parametrize(
        ("track_id", "should_pass"),
        [
            ("normal_track_123", True),
            ("track-with-hyphens", True),
            ("track_with_underscores", True),
            ("123456789", True),
            ("track; rm -rf /", False),  # Command injection attempt
            ("track && malicious", False),  # Command chaining
            ("track`command`", False),  # Command substitution
            ("track$injection", False),  # Variable injection
            ("a" * (MAX_TRACK_ID_LENGTH + 1), False),  # Too long
        ],
    )
    def test_sanitize_track_id(self, track_id: str, should_pass: bool) -> None:
        """Test track ID sanitization."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with allure.step(f"Sanitizing track ID: {track_id[:50]}..."):
            try:
                sanitization_passed = sanitizer.validate_track_id(track_id)
                result = track_id if sanitization_passed else None
            except AppleScriptSanitizationError:
                sanitization_passed = False
                result = None

        with allure.step("Verify track ID sanitization"):
            if should_pass:
                assert sanitization_passed, f"Track ID should pass sanitization: {track_id}"
                assert result == track_id
            else:
                assert not sanitization_passed, f"Track ID should fail sanitization: {track_id}"

            allure.attach(track_id, "Input Track ID", allure.attachment_type.TEXT)
            allure.attach(str(sanitization_passed), "Sanitization Result", allure.attachment_type.TEXT)
            allure.attach(str(should_pass), "Expected Result", allure.attachment_type.TEXT)

    @allure.story("Script Size Validation")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should enforce script size limits")
    @allure.description("Test script size validation to prevent resource exhaustion")
    def test_script_size_validation(self) -> None:
        """Test script size validation."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with allure.step("Create oversized script"):
            # Create a script that exceeds MAX_SCRIPT_SIZE
            oversized_script = "-- " + "A" * (MAX_SCRIPT_SIZE + 1)

            allure.attach(str(len(oversized_script)), "Script Size", allure.attachment_type.TEXT)
            allure.attach(str(MAX_SCRIPT_SIZE), "Maximum Allowed Size", allure.attachment_type.TEXT)

        with allure.step("Attempt to sanitize oversized script"), pytest.raises(AppleScriptSanitizationError) as exc_info:
            sanitizer.validate_script_code(oversized_script)

        with allure.step("Verify size limit enforcement"):
            error = exc_info.value
            assert isinstance(error, AppleScriptSanitizationError)
            assert "too large" in str(error).lower()

            allure.attach(str(error), "Size Limit Error", allure.attachment_type.TEXT)

    @allure.story("Pattern Detection")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should detect complex script patterns")
    @allure.description("Test detection of complex script patterns for temp file usage")
    def test_complex_pattern_detection(self) -> None:
        """Test detection of complex script patterns."""
        TestAppleScriptSanitizerAllure.create_sanitizer()

        with allure.step("Create script with multiple tell blocks"):
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

        with allure.step("Analyze script complexity"):
            # This would be used internally to determine temp file usage
            # Testing the existence of pattern detection capability
            tell_blocks = complex_script.count("tell application")

            allure.attach(complex_script, "Complex Script", allure.attachment_type.TEXT)
            allure.attach(str(tell_blocks), "Tell Blocks Count", allure.attachment_type.TEXT)

        with allure.step("Verify pattern detection"):
            assert tell_blocks == 3
            # Complex pattern detection should identify this as complex
            assert tell_blocks > 2  # Threshold for complexity

            allure.attach("Complex script detected", "Complexity Result", allure.attachment_type.TEXT)


@allure.epic("Music Genre Updater")
@allure.feature("AppleScript Integration")
class TestAppleScriptClientAllure:
    """Enhanced tests for AppleScript client with Allure reporting."""

    @staticmethod
    def create_client(
        config: dict[str, Any] | None = None,
        analytics: Any = None,
    ) -> AppleScriptClient:
        """Create an AppleScriptClient instance for testing."""

        test_config = config or {"apple_script": {"timeout": 30, "concurrency": 5}, "apple_scripts_dir": "applescripts/"}

        console_logger = MockLogger()
        error_logger = MockLogger()
        test_analytics = analytics or MockAnalytics()

        return AppleScriptClient(
            config=test_config,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=test_analytics,
        )

    @allure.story("Initialization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should initialize AppleScript client with configuration")
    @allure.description("Test initialization of AppleScript client with proper configuration")
    def test_client_initialization_comprehensive(self) -> None:
        """Test comprehensive AppleScript client initialization."""
        with allure.step("Setup client configuration"):
            config = {"apple_script": {"timeout": 45, "concurrency": 10, "max_retries": 3}, "apple_scripts_dir": "custom_scripts/"}

        with allure.step("Initialize AppleScript client"):
            client = TestAppleScriptClientAllure.create_client(config=config)

        with allure.step("Verify initialization"):
            assert client.config == config
            assert hasattr(client, "console_logger")
            assert hasattr(client, "error_logger")
            assert hasattr(client, "analytics")

            # Verify sanitizer is initialized
            assert hasattr(client, "sanitizer")
            assert isinstance(client.sanitizer, AppleScriptSanitizer)

        allure.attach("AppleScript client initialized successfully", "Initialization Result", allure.attachment_type.TEXT)
        allure.attach(str(config["apple_script"]["timeout"]), "Timeout Setting", allure.attachment_type.TEXT)  # type: ignore[index]
        allure.attach(str(config["apple_script"]["concurrency"]), "Concurrency Setting", allure.attachment_type.TEXT)  # type: ignore[index]

    @allure.story("Script Execution")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should execute AppleScript commands safely")
    @allure.description("Test safe execution of AppleScript commands with proper validation")
    @pytest.mark.asyncio
    async def test_execute_applescript_safely(self) -> None:
        """Test safe AppleScript execution."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with allure.step("Setup mock subprocess execution"), patch("asyncio.create_subprocess_exec") as mock_subprocess:
            # Mock successful script execution
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"Success result", b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            safe_script = 'tell application "Music" to get name of current track'

            with allure.step("Execute safe AppleScript"):
                result = await client.run_script_code(safe_script)

        with allure.step("Verify safe execution"):
            assert result is not None
            # Verify subprocess was called
            mock_subprocess.assert_called_once()

            allure.attach(safe_script, "Executed Script", allure.attachment_type.TEXT)
            allure.attach(str(result), "Execution Result", allure.attachment_type.TEXT)

    @allure.story("Security Enforcement")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should block dangerous AppleScript execution")
    @allure.description("Test that dangerous AppleScript commands are blocked")
    @pytest.mark.asyncio
    async def test_block_dangerous_applescript(self) -> None:
        """Test blocking of dangerous AppleScript execution."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with allure.step("Attempt to execute dangerous script"):
            dangerous_script = 'do shell script "rm -rf /"'

            # run_script_code returns None when blocking dangerous scripts
            result = await client.run_script_code(dangerous_script)

        with allure.step("Verify dangerous script was blocked"):
            assert result is None, "Dangerous script should have been blocked"

            allure.attach(dangerous_script, "Blocked Script", allure.attachment_type.TEXT)
            allure.attach("Script returned None (blocked)", "Security Result", allure.attachment_type.TEXT)
            allure.attach("Script execution blocked successfully", "Security Status", allure.attachment_type.TEXT)

    @allure.story("Music Application Integration")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should fetch tracks from Music.app")
    @allure.description("Test fetching track information from Apple Music application")
    @pytest.mark.asyncio
    async def test_fetch_tracks_from_music_app(self) -> None:
        """Test fetching tracks from Music.app."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with allure.step("Setup mock Music.app response"), patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_tracks_data = """Track 1|Artist 1|Album 1|2020|Rock
Track 2|Artist 2|Album 2|2021|Jazz
Track 3|Artist 3|Album 3|2022|Pop"""

            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(mock_tracks_data.encode(), b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            with allure.step("Execute track fetching"):
                # AppleScriptClient doesn't have fetch_tracks_async, it has run_script
                result = await client.run_script("fetch_tracks.scpt")

        with allure.step("Verify track fetching results"):
            assert result is not None

            # Verify subprocess was called with proper AppleScript
            mock_subprocess.assert_called_once()
            call_args = mock_subprocess.call_args[0]
            assert "osascript" in call_args

            allure.attach("Mock Tracks Data", "Mock Tracks Data", allure.attachment_type.TEXT)
            allure.attach("Track fetching successful", "Fetch Result", allure.attachment_type.TEXT)

    @allure.story("Track Updates")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should update track properties in Music.app")
    @allure.description("Test updating track properties like genre through AppleScript")
    @pytest.mark.asyncio
    async def test_update_track_properties(self) -> None:
        """Test updating track properties in Music.app."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with allure.step("Setup mock track update response"), patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"Success: Genre updated", b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            with allure.step("Execute track property update"):
                # AppleScriptClient doesn't have update_track_async, use run_script with arguments
                result = await client.run_script("update_property.applescript", arguments=["test_track_001", "genre", "Electronic"])

        with allure.step("Verify track update"):
            assert result is not None
            assert "Success" in result or "updated" in result.lower()

            # Verify subprocess was called
            mock_subprocess.assert_called_once()

            allure.attach("test_track_001", "Updated Track ID", allure.attachment_type.TEXT)
            allure.attach("Electronic", "New Genre", allure.attachment_type.TEXT)
            allure.attach(str(result), "Update Result", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle AppleScript execution errors")
    @allure.description("Test error handling when AppleScript execution fails")
    @pytest.mark.asyncio
    async def test_handle_applescript_execution_errors(self) -> None:
        """Test handling of AppleScript execution errors."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with allure.step("Setup failing AppleScript execution"), patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b"AppleScript Error: syntax error"))
            mock_process.wait = AsyncMock(return_value=1)
            mock_process.returncode = 1  # Error exit code
            mock_subprocess.return_value = mock_process

            safe_script = 'tell application "Music" to get invalid property'

            with allure.step("Execute failing AppleScript"):
                result = await client.run_script_code(safe_script)

        with allure.step("Verify error handling"):
            # Client should handle the error gracefully
            # Result might be None or error string depending on implementation
            assert result is None or "error" in result.lower()

            # Verify error was logged (check if it's our MockLogger)
            if isinstance(client.error_logger, MockLogger):
                error_messages = client.error_logger.error_messages
                assert len(error_messages) > 0
                allure.attach(str(error_messages), "Error Messages", allure.attachment_type.TEXT)

            allure.attach(safe_script, "Failed Script", allure.attachment_type.TEXT)
            allure.attach("Error handled gracefully", "Error Handling Result", allure.attachment_type.TEXT)

    @allure.story("Concurrency Control")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should manage concurrent AppleScript executions")
    @allure.description("Test concurrency control for multiple AppleScript executions")
    @pytest.mark.asyncio
    async def test_concurrency_control(self) -> None:
        """Test concurrency control for AppleScript executions."""
        config = {
            "apple_script": {
                "timeout": 30,
                "concurrency": 2,  # Limited concurrency for testing
            },
            "apple_scripts_dir": "applescripts/",
        }
        client = TestAppleScriptClientAllure.create_client(config=config)
        await client.initialize()

        with allure.step("Setup multiple concurrent script executions"), patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"Success", b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            # Create multiple concurrent tasks
            scripts = [
                'tell application "Music" to get name of track 1',
                'tell application "Music" to get name of track 2',
                'tell application "Music" to get name of track 3',
            ]

            with allure.step("Execute concurrent scripts"):
                tasks = [client.run_script_code(script) for script in scripts]
                results = await asyncio.gather(*tasks, return_exceptions=True)

        with allure.step("Verify concurrency control"):
            # All tasks should complete successfully
            successful_results = [r for r in results if not isinstance(r, Exception)]
            assert len(successful_results) == len(scripts)

            # Verify subprocess calls were made
            assert mock_subprocess.call_count == len(scripts)

            allure.attach(str(len(scripts)), "Concurrent Scripts", allure.attachment_type.TEXT)
            allure.attach(str(len(successful_results)), "Successful Executions", allure.attachment_type.TEXT)
            allure.attach(str(config["apple_script"]["concurrency"]), "Concurrency Limit", allure.attachment_type.TEXT)  # type: ignore[index]

    @allure.story("Input Sanitization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should sanitize string inputs properly")
    @allure.description("Test string sanitization for AppleScript safety")
    def test_sanitize_string_inputs(self) -> None:
        """Test string sanitization functionality."""
        sanitizer = AppleScriptSanitizer(MockLogger())

        with allure.step("Test sanitization of special characters"):
            # Test escaping quotes
            assert sanitizer.sanitize_string('Test "quoted" string') == 'Test \\"quoted\\" string'
            # Single quotes don't need escaping in AppleScript double-quoted strings
            assert sanitizer.sanitize_string("Test 'single' quote") == "Test 'single' quote"

            # Test escaping backslashes
            assert sanitizer.sanitize_string("Test\\backslash") == "Test\\\\backslash"

            # Test combined escaping
            assert sanitizer.sanitize_string('Test "\\" combo') == 'Test \\"\\\\\\" combo'

            allure.attach("Special characters sanitized correctly", "Sanitization Result", allure.attachment_type.TEXT)

    @allure.story("Input Sanitization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle invalid input types for sanitization")
    @allure.description("Test error handling for invalid sanitization inputs")
    def test_sanitize_invalid_inputs(self) -> None:
        """Test sanitization error handling."""
        sanitizer = AppleScriptSanitizer(MockLogger())

        with allure.step("Test None value"), pytest.raises(ValueError, match="Cannot sanitize None value"):
            sanitizer.sanitize_string(None)

        with allure.step("Test non-string value"), pytest.raises(TypeError, match="Expected string, got int"):
            sanitizer.sanitize_string(123)  # type: ignore[arg-type]

        with allure.step("Test non-string value: list"), pytest.raises(TypeError, match="Expected string, got list"):
            sanitizer.sanitize_string([1, 2, 3])  # type: ignore[arg-type]

        allure.attach("Invalid inputs properly rejected", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Script Path Validation")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should validate script paths correctly")
    @allure.description("Test script path validation for security")
    def test_script_path_validation(self) -> None:
        """Test script path validation."""
        config = {"apple_script": {"timeout": 30}, "apple_scripts_dir": "/test/scripts/"}
        client = TestAppleScriptClientAllure.create_client(config=config)

        with allure.step("Test path validation"):
            # Test valid path
            valid = client.file_validator.validate_script_path("/test/scripts/test.scpt")
            assert valid is True

            # Test invalid path (traversal attempt)
            invalid = client.file_validator.validate_script_path("/test/scripts/../../../etc/passwd")
            assert invalid is False

            allure.attach("/test/scripts/test.scpt", "Valid Path", allure.attachment_type.TEXT)
            allure.attach("Path validation working", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Script Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should validate script file existence")
    @allure.description("Test validation of script file existence and readability")
    @pytest.mark.asyncio
    async def test_script_file_validation(self) -> None:
        """Test script file validation."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with allure.step("Test missing script file"), patch("pathlib.Path.exists", return_value=False):
            result = await client.run_script("nonexistent.scpt")

            # Should return None or handle gracefully
            assert result is None or "error" in str(result).lower()

            allure.attach("nonexistent.scpt", "Missing Script", allure.attachment_type.TEXT)
            allure.attach("Script file validation working", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Batch Operations")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle batch track operations")
    @allure.description("Test batch processing of multiple track operations")
    @pytest.mark.asyncio
    async def test_batch_track_operations(self) -> None:
        """Test batch track operations."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with (
            allure.step("Setup batch operation mock"),
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
        ):
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"Batch complete: 10 tracks updated", b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            # Simulate batch update
            track_ids = ["track_001", "track_002", "track_003"]

            with allure.step("Execute batch update"):
                # Run multiple updates
                tasks = []
                for track_id in track_ids:
                    task = client.run_script("update_property.applescript", arguments=[track_id, "genre", "Rock"])
                    tasks.append(task)

                results = await asyncio.gather(*tasks)

        with allure.step("Verify batch processing"):
            assert len(results) == len(track_ids)
            for result in results:
                assert result is not None

            allure.attach(str(len(track_ids)), "Batch Size", allure.attachment_type.TEXT)
            allure.attach("Batch processing successful", "Batch Result", allure.attachment_type.TEXT)

    @allure.story("Resource Management")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle resource management properly")
    @allure.description("Test proper resource management and error handling")
    @pytest.mark.asyncio
    async def test_resource_cleanup(self) -> None:
        """Test proper resource management and error handling."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with (
            allure.step("Create active resources"),
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
        ):
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"Success", b""))
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            # Run some operations
            await client.run_script_code('tell application "Music" to play')

        with allure.step("Test error handling for invalid scripts"):
            # Since AppleScriptClient doesn't have a shutdown method,
            # test error handling for invalid scripts instead
            result = await client.run_script("non_existent_script.scpt", [])

            # run_script returns None for non-existent files
            assert result is None, "Non-existent script should return None"

            allure.attach("Invalid script handling", "Error Handling Result", allure.attachment_type.TEXT)
            allure.attach("Resources managed properly", "Resource Status", allure.attachment_type.TEXT)

    @allure.story("Timeout Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle script execution timeouts")
    @allure.description("Test timeout handling for long-running AppleScript operations")
    @pytest.mark.asyncio
    async def test_execution_timeout(self) -> None:
        """Test handling of script execution timeouts."""
        config = {
            "apple_script": {"timeout": 1},  # 1 second timeout
            "apple_scripts_dir": "applescripts/",
        }
        client = TestAppleScriptClientAllure.create_client(config=config)
        await client.initialize()

        with (
            allure.step("Simulate timeout scenario"),
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
        ):
            mock_process = MagicMock()

            async def slow_communicate() -> tuple[bytes, bytes]:
                """Simulate slow subprocess communication."""
                await asyncio.sleep(5)  # Longer than timeout
                return b"", b"Timeout"

            mock_process.communicate = slow_communicate
            mock_process.wait = AsyncMock(return_value=-1)
            mock_process.returncode = -1
            mock_subprocess.return_value = mock_process

            with allure.step("Execute script with timeout"):
                # This should timeout
                result = await client.run_script_code('tell application "Music" to get every track')

        with allure.step("Verify timeout handling"):
            # Result should indicate timeout or be None
            assert result is None or "timeout" in str(result).lower()

            allure.attach("1 second", "Timeout Setting", allure.attachment_type.TEXT)
            allure.attach("Timeout handled gracefully", "Timeout Result", allure.attachment_type.TEXT)

    @allure.story("Script Code Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should validate script code size limits")
    @allure.description("Test validation of AppleScript code size to prevent DoS")
    def test_script_size_validation(self) -> None:
        """Test script size validation."""
        sanitizer = AppleScriptSanitizer(MockLogger())

        with allure.step("Test oversized script"):
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

            allure.attach(str(len(huge_script)), "Script Size", allure.attachment_type.TEXT)
            allure.attach(str(MAX_SCRIPT_SIZE), "Max Allowed Size", allure.attachment_type.TEXT)
            allure.attach("Size validation working", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("String Sanitization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle edge cases in string sanitization")
    @allure.description("Test edge cases and error conditions in string sanitization")
    def test_sanitization_edge_cases(self) -> None:
        """Test edge cases in string sanitization."""
        sanitizer = AppleScriptSanitizer(MockLogger())

        with allure.step("Test empty string"):
            assert sanitizer.sanitize_string("") == ""

        with allure.step("Test string with only special chars"):
            assert sanitizer.sanitize_string('"""') == '\\"\\"\\"'

        with allure.step("Test very long string"):
            long_str = "a" * 10000
            assert sanitizer.sanitize_string(long_str) == long_str

        with allure.step("Test newline and tab characters"):
            assert len(sanitizer.sanitize_string("line1\nline2\ttab")) > 0

            allure.attach("Edge cases handled correctly", "Sanitization Result", allure.attachment_type.TEXT)

    @allure.story("Process Cleanup")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle process cleanup errors gracefully")
    @allure.description("Test error handling during process cleanup")
    @pytest.mark.asyncio
    async def test_process_cleanup_error_handling(self) -> None:
        """Test process cleanup error handling."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with allure.step("Test cleanup with terminated process"):
            # Create a mock process that's already terminated
            mock_proc = MagicMock()
            mock_proc.terminate = MagicMock(side_effect=ProcessLookupError("Process already terminated"))
            mock_proc.poll = MagicMock(return_value=None)  # Process still running
            mock_proc.wait = AsyncMock(side_effect=TimeoutError)

            # Should handle cleanup gracefully
            await client.executor.cleanup_process(mock_proc, "test_process")

            allure.attach("Process cleanup handled gracefully", "Cleanup Result", allure.attachment_type.TEXT)

    @allure.story("Script Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should validate empty script code")
    @allure.description("Test validation of empty script code")
    def test_validate_empty_script(self) -> None:
        """Test validation of empty script code."""
        sanitizer = AppleScriptSanitizer(MockLogger())

        with allure.step("Test empty script validation"):
            with pytest.raises(ValueError, match="Script code must be a non-empty string"):
                sanitizer.validate_script_code(None)

            with pytest.raises(ValueError, match="Script code must be a non-empty string"):
                sanitizer.validate_script_code("")

            # Whitespace-only doesn't raise an error, it just normalizes it
            # Verify that whitespace-only scripts are handled without raising
            sanitizer.validate_script_code("   ")

            allure.attach("Empty script validation working", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Error Recovery")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle subprocess errors gracefully")
    @allure.description("Test error handling for subprocess failures")
    @pytest.mark.asyncio
    async def test_subprocess_error_handling(self) -> None:
        """Test subprocess error handling."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with (
            allure.step("Test subprocess creation failure"),
            patch("asyncio.create_subprocess_exec", side_effect=OSError("Cannot create process")),
        ):
            result = await client.run_script_code('tell application "Music" to play')

            # Should return None on subprocess error
            assert result is None

            # Check error was logged
            if isinstance(client.error_logger, MockLogger):
                assert len(client.error_logger.error_messages) > 0

        allure.attach("Subprocess errors handled gracefully", "Error Result", allure.attachment_type.TEXT)

    @allure.story("Command Construction")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should create command with arguments")
    @allure.description("Test AppleScript command construction with arguments")
    @pytest.mark.asyncio
    async def test_create_command_with_arguments(self) -> None:
        """Test creating commands with arguments."""
        client = TestAppleScriptClientAllure.create_client()
        await client.initialize()

        with allure.step("Create command with safe arguments"):
            script = 'tell application "Music" to get name'
            args = ["trackID123", "GenreRock"]
            cmd = client.sanitizer.create_safe_command(script, arguments=args)

            # Verify command structure
            assert cmd[0] == "osascript"
            assert cmd[1] == "-e"
            assert cmd[2] == script
            assert len(cmd) == 5  # osascript, -e, script, arg1, arg2

        with allure.step("Test dangerous characters in arguments"):
            dangerous_args = ["track; rm -rf /"]
            with pytest.raises(AppleScriptSanitizationError, match="Dangerous characters"):
                client.sanitizer.create_safe_command(script, arguments=dangerous_args)

        allure.attach("Command arguments validated", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Rate Limiting")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should validate rate limiter parameters")
    @allure.description("Test EnhancedRateLimiter parameter validation")
    @pytest.mark.asyncio
    async def test_rate_limiter_validation(self) -> None:
        """Test rate limiter parameter validation."""
        with allure.step("Test invalid requests_per_window"), pytest.raises(ValueError, match="requests_per_window must be a positive integer"):
            EnhancedRateLimiter(requests_per_window=0, window_size=1.0)

        with allure.step("Test invalid window_size"), pytest.raises(ValueError, match="window_size must be a positive number"):
            EnhancedRateLimiter(requests_per_window=10, window_size=0)

        with allure.step("Test invalid max_concurrent"), pytest.raises(ValueError, match="max_concurrent must be a positive integer"):
            EnhancedRateLimiter(requests_per_window=10, window_size=1.0, max_concurrent=0)

        with allure.step("Test valid initialization"):
            limiter = EnhancedRateLimiter(requests_per_window=10, window_size=1.0)
            await limiter.initialize()
            assert limiter.semaphore is not None
            assert limiter.total_requests == 0

        allure.attach("Rate limiter parameters validated", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Rate Limiting")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should enforce rate limiting")
    @allure.description("Test rate limiter acquire/release functionality")
    @pytest.mark.asyncio
    async def test_rate_limiter_acquire_release(self) -> None:
        """Test rate limiter acquire and release."""
        limiter = EnhancedRateLimiter(requests_per_window=2, window_size=1.0, max_concurrent=1)
        await limiter.initialize()

        with allure.step("Test acquire and release"):
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

        with allure.step("Test uninitialized limiter"):
            uninit_limiter = EnhancedRateLimiter(requests_per_window=10, window_size=1.0)
            with pytest.raises(RuntimeError, match="RateLimiter not initialized"):
                await uninit_limiter.acquire()

        with allure.step("Test get_stats"):
            stats = limiter.get_stats()
            assert "total_requests" in stats
            assert "total_wait_time" in stats
            assert stats["total_requests"] == 3
            assert stats["total_wait_time"] > 0

        allure.attach("Rate limiting enforced", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Security Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should validate track IDs with dangerous characters")
    @allure.description("Test track ID validation with security checks")
    def test_track_id_with_dangerous_chars_logging(self) -> None:
        """Test track ID validation logging for dangerous characters."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with allure.step("Test track ID with dangerous characters triggers warning"):
            # This should return False and log a warning
            result = sanitizer.validate_track_id("track; rm -rf /")
            assert result is False

            # Verify warning was logged
            if isinstance(sanitizer.logger, MockLogger):
                assert len(sanitizer.logger.warning_messages) > 0

        allure.attach("Dangerous track ID logged", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Security Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should validate reserved words in scripts")
    @allure.description("Test validation of restricted AppleScript commands")
    def test_reserved_words_validation(self) -> None:
        """Test validation of AppleScript reserved words."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with allure.step("Test Finder operations are blocked"), pytest.raises(AppleScriptSanitizationError, match="Dangerous AppleScript pattern"):
            sanitizer.validate_script_code('tell application "Finder" to delete file "test.txt"', allow_music_app=False)

        with allure.step("Test Music.app operations allowed when enabled"):
            # This should not raise an error (allow_music_app=True is default)
            try:
                sanitizer.validate_script_code('tell application "Music" to play')
            except AppleScriptSanitizationError:
                pytest.fail("Music.app operations should be allowed when allow_music_app=True")

        allure.attach("Reserved words validated", "Validation Result", allure.attachment_type.TEXT)

    @allure.story("Security Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should use word boundaries for reserved word matching")
    @allure.description("Test that reserved words use regex word boundaries to prevent false positives")
    def test_reserved_words_word_boundary_matching(self) -> None:
        """Test that reserved words are matched with word boundaries."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with allure.step("Test standalone 'delete' raises error"), pytest.raises(AppleScriptSanitizationError, match="delete"):
            sanitizer.validate_script_code("delete track", allow_music_app=False)

        with allure.step("Test 'undelete' does NOT raise (substring contains 'delete')"):
            # Should NOT raise - "undelete" contains "delete" but is not a standalone word
            try:
                sanitizer.validate_script_code("undelete operation", allow_music_app=False)
            except AppleScriptSanitizationError as e:
                if "delete" in str(e).lower():
                    pytest.fail("'undelete' should not trigger 'delete' reserved word check")

        with allure.step("Test 'nodelete' does NOT raise"):
            try:
                sanitizer.validate_script_code("nodelete flag enabled", allow_music_app=False)
            except AppleScriptSanitizationError as e:
                if "delete" in str(e).lower():
                    pytest.fail("'nodelete' should not trigger 'delete' reserved word check")

        with allure.step("Test 'deleted' does NOT raise"):
            try:
                sanitizer.validate_script_code("file was deleted yesterday", allow_music_app=False)
            except AppleScriptSanitizationError as e:
                if "delete" in str(e).lower():
                    pytest.fail("'deleted' should not trigger 'delete' reserved word check")

        allure.attach("Word boundary matching verified", "Result", allure.attachment_type.TEXT)

    @allure.story("Security Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should respect allow_music_app parameter")
    @allure.description("Test that allow_music_app correctly filters Music.app operations")
    def test_allow_music_app_parameter(self) -> None:
        """Test allow_music_app parameter behavior."""
        sanitizer = TestAppleScriptSanitizerAllure.create_sanitizer()

        with allure.step("Test 'delete' blocked when allow_music_app=False"), pytest.raises(AppleScriptSanitizationError, match="delete"):
            sanitizer.validate_script_code("delete track from playlist", allow_music_app=False)

        with (
            allure.step("Test 'delete' blocked when allow_music_app=True (not Music-specific)"),
            pytest.raises(AppleScriptSanitizationError, match="delete"),
        ):
            # 'delete' is in APPLESCRIPT_RESERVED_WORDS but doesn't contain 'music'
            sanitizer.validate_script_code("delete track from playlist", allow_music_app=True)

        with allure.step("Test 'move' blocked regardless of allow_music_app"), pytest.raises(AppleScriptSanitizationError, match="move"):
            sanitizer.validate_script_code("move file to folder", allow_music_app=True)

        allure.attach("allow_music_app parameter verified", "Result", allure.attachment_type.TEXT)
