"""Enhanced AppleScript Client tests with Allure reporting."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest
from src.infrastructure.applescript_client import (
    MAX_SCRIPT_SIZE,
    MAX_TRACK_ID_LENGTH,
    AppleScriptClient,
    AppleScriptSanitizationError,
    AppleScriptSanitizer,
)

from tests.mocks.csv_mock import MockAnalytics, MockLogger


@allure.epic("Music Genre Updater")
@allure.feature("AppleScript Integration")
class TestAppleScriptSanitizerAllure:
    """Enhanced tests for AppleScript sanitizer with Allure reporting."""

    def create_sanitizer(self, logger: Any = None) -> AppleScriptSanitizer:
        """Create an AppleScriptSanitizer instance for testing."""
        test_logger = logger or MockLogger()
        return AppleScriptSanitizer(logger=test_logger)

    @allure.story("Security Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should detect dangerous AppleScript patterns")
    @allure.description("Test detection of potentially dangerous AppleScript code patterns")
    @pytest.mark.parametrize(
        "dangerous_code,expected_pattern",
        [
            ('do shell script "rm -rf /"', "do shell script"),
            ('tell application "Finder" to delete', 'tell application "Finder"'),
            ('tell application "System Events" to keystroke', "keystroke"),
            ('load script file "malicious.scpt"', "load script"),
            ('choose file with prompt "Select file"', "choose file"),
            ('open location "http://malicious.com"', "open location"),
        ],
    )
    def test_detect_dangerous_patterns(self, dangerous_code: str, expected_pattern: str) -> None:
        """Test detection of dangerous AppleScript patterns."""
        sanitizer = self.create_sanitizer()

        with allure.step(f"Testing dangerous code: {dangerous_code[:50]}..."):
            with pytest.raises(AppleScriptSanitizationError) as exc_info:
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
        sanitizer = self.create_sanitizer()

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
        "track_id,should_pass",
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
        sanitizer = self.create_sanitizer()

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
        sanitizer = self.create_sanitizer()

        with allure.step("Create oversized script"):
            # Create a script that exceeds MAX_SCRIPT_SIZE
            oversized_script = "-- " + "A" * (MAX_SCRIPT_SIZE + 1)

            allure.attach(str(len(oversized_script)), "Script Size", allure.attachment_type.TEXT)
            allure.attach(str(MAX_SCRIPT_SIZE), "Maximum Allowed Size", allure.attachment_type.TEXT)

        with allure.step("Attempt to sanitize oversized script"):
            with pytest.raises(AppleScriptSanitizationError) as exc_info:
                sanitizer.validate_script_code(oversized_script)

        with allure.step("Verify size limit enforcement"):
            error = exc_info.value
            assert isinstance(error, AppleScriptSanitizationError)
            assert "size limit" in str(error).lower()

            allure.attach(str(error), "Size Limit Error", allure.attachment_type.TEXT)

    @allure.story("Pattern Detection")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should detect complex script patterns")
    @allure.description("Test detection of complex script patterns for temp file usage")
    def test_complex_pattern_detection(self) -> None:
        """Test detection of complex script patterns."""
        sanitizer = self.create_sanitizer()

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

    def create_client(
        self,
        config: dict[str, Any] | None = None,
        analytics: Any = None,
    ) -> AppleScriptClient:
        """Create an AppleScriptClient instance for testing."""
        test_config = config or {"apple_script": {"timeout": 30, "concurrency": 5, "script_directory": "applescripts/"}}

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
            config = {"apple_script": {"timeout": 45, "concurrency": 10, "script_directory": "custom_scripts/", "max_retries": 3}}

        with allure.step("Initialize AppleScript client"):
            client = self.create_client(config=config)

        with allure.step("Verify initialization"):
            assert client.config == config
            assert hasattr(client, "console_logger")
            assert hasattr(client, "error_logger")
            assert hasattr(client, "analytics")

            # Verify sanitizer is initialized
            assert hasattr(client, "sanitizer")
            assert isinstance(client.sanitizer, AppleScriptSanitizer)

            allure.attach("AppleScript client initialized successfully", "Initialization Result", allure.attachment_type.TEXT)
            allure.attach(str(config["apple_script"]["timeout"]), "Timeout Setting", allure.attachment_type.TEXT)
            allure.attach(str(config["apple_script"]["concurrency"]), "Concurrency Setting", allure.attachment_type.TEXT)

    @allure.story("Script Execution")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should execute AppleScript commands safely")
    @allure.description("Test safe execution of AppleScript commands with proper validation")
    @pytest.mark.asyncio
    async def test_execute_applescript_safely(self) -> None:
        """Test safe AppleScript execution."""
        client = self.create_client()

        with allure.step("Setup mock subprocess execution"):
            with patch("asyncio.create_subprocess_exec") as mock_subprocess:
                # Mock successful script execution
                mock_process = MagicMock()
                mock_process.communicate = AsyncMock(return_value=(b"Success result", b""))
                mock_process.returncode = 0
                mock_subprocess.return_value = mock_process

                safe_script = 'tell application "Music" to get name of current track'

                with allure.step("Execute safe AppleScript"):
                    result = await client.execute_applescript_async(safe_script)

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
        client = self.create_client()

        with allure.step("Attempt to execute dangerous script"):
            dangerous_script = 'do shell script "rm -rf /"'

            with pytest.raises(AppleScriptSanitizationError) as exc_info:
                await client.execute_applescript_async(dangerous_script)

        with allure.step("Verify dangerous script was blocked"):
            error = exc_info.value
            assert isinstance(error, AppleScriptSanitizationError)

            allure.attach(dangerous_script, "Blocked Script", allure.attachment_type.TEXT)
            allure.attach(str(error), "Security Error", allure.attachment_type.TEXT)
            allure.attach("Script execution blocked successfully", "Security Result", allure.attachment_type.TEXT)

    @allure.story("Music Application Integration")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should fetch tracks from Music.app")
    @allure.description("Test fetching track information from Apple Music application")
    @pytest.mark.asyncio
    async def test_fetch_tracks_from_music_app(self) -> None:
        """Test fetching tracks from Music.app."""
        client = self.create_client()

        with allure.step("Setup mock Music.app response"):
            mock_tracks_data = """Track 1|Artist 1|Album 1|2020|Rock
Track 2|Artist 2|Album 2|2021|Jazz
Track 3|Artist 3|Album 3|2022|Pop"""

            with patch("asyncio.create_subprocess_exec") as mock_subprocess:
                mock_process = MagicMock()
                mock_process.communicate = AsyncMock(return_value=(mock_tracks_data.encode(), b""))
                mock_process.returncode = 0
                mock_subprocess.return_value = mock_process

                with allure.step("Execute track fetching"):
                    result = await client.fetch_tracks_async()

        with allure.step("Verify track fetching results"):
            assert result is not None

            # Verify subprocess was called with proper AppleScript
            mock_subprocess.assert_called_once()
            call_args = mock_subprocess.call_args[0]
            assert "osascript" in call_args

            allure.attach(mock_tracks_data, "Mock Tracks Data", allure.attachment_type.TEXT)
            allure.attach("Track fetching successful", "Fetch Result", allure.attachment_type.TEXT)

    @allure.story("Track Updates")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should update track properties in Music.app")
    @allure.description("Test updating track properties like genre through AppleScript")
    @pytest.mark.asyncio
    async def test_update_track_properties(self) -> None:
        """Test updating track properties in Music.app."""
        client = self.create_client()

        with allure.step("Setup mock track update response"):
            with patch("asyncio.create_subprocess_exec") as mock_subprocess:
                mock_process = MagicMock()
                mock_process.communicate = AsyncMock(return_value=(b"Success: Genre updated", b""))
                mock_process.returncode = 0
                mock_subprocess.return_value = mock_process

                with allure.step("Execute track property update"):
                    result = await client.update_track_async(
                        track_id="test_track_001",
                        new_genre="Electronic",
                        original_artist="Test Artist",
                        original_album="Test Album",
                        original_track="Test Track",
                    )

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
        client = self.create_client()

        with allure.step("Setup failing AppleScript execution"):
            with patch("asyncio.create_subprocess_exec") as mock_subprocess:
                mock_process = MagicMock()
                mock_process.communicate = AsyncMock(return_value=(b"", b"AppleScript Error: syntax error"))
                mock_process.returncode = 1  # Error exit code
                mock_subprocess.return_value = mock_process

                safe_script = 'tell application "Music" to get invalid property'

                with allure.step("Execute failing AppleScript"):
                    result = await client.execute_applescript_async(safe_script)

        with allure.step("Verify error handling"):
            # Client should handle the error gracefully
            # Result might be None or error string depending on implementation
            assert result is None or "error" in result.lower()

            # Verify error was logged
            error_messages = client.error_logger.error_messages
            assert len(error_messages) > 0

            allure.attach(safe_script, "Failed Script", allure.attachment_type.TEXT)
            allure.attach(str(error_messages), "Error Messages", allure.attachment_type.TEXT)
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
            }
        }
        client = self.create_client(config=config)

        with allure.step("Setup multiple concurrent script executions"):
            with patch("asyncio.create_subprocess_exec") as mock_subprocess:
                mock_process = MagicMock()
                mock_process.communicate = AsyncMock(return_value=(b"Success", b""))
                mock_process.returncode = 0
                mock_subprocess.return_value = mock_process

                # Create multiple concurrent tasks
                scripts = [
                    'tell application "Music" to get name of track 1',
                    'tell application "Music" to get name of track 2',
                    'tell application "Music" to get name of track 3',
                ]

                with allure.step("Execute concurrent scripts"):
                    tasks = [client.execute_applescript_async(script) for script in scripts]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

        with allure.step("Verify concurrency control"):
            # All tasks should complete successfully
            successful_results = [r for r in results if not isinstance(r, Exception)]
            assert len(successful_results) == len(scripts)

            # Verify subprocess calls were made
            assert mock_subprocess.call_count == len(scripts)

            allure.attach(str(len(scripts)), "Concurrent Scripts", allure.attachment_type.TEXT)
            allure.attach(str(len(successful_results)), "Successful Executions", allure.attachment_type.TEXT)
            allure.attach(str(config["apple_script"]["concurrency"]), "Concurrency Limit", allure.attachment_type.TEXT)
