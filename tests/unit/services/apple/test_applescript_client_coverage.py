"""Additional AppleScriptClient tests for coverage improvement."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.apple.applescript_client import AppleScriptClient


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create test console logger."""
    return logging.getLogger("test.applescript.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create test error logger."""
    return logging.getLogger("test.applescript.error")


@pytest.fixture
def base_config(tmp_path: Path) -> dict[str, Any]:
    """Create base config with scripts directory."""
    scripts_dir = tmp_path / "applescripts"
    scripts_dir.mkdir()
    # Create required scripts
    (scripts_dir / "update_property.applescript").write_text("-- test script")
    (scripts_dir / "fetch_tracks.applescript").write_bytes(b"-- test script")

    return {
        "apple_scripts_dir": str(scripts_dir),
        "apple_script_concurrency": 2,
        "applescript_timeout_seconds": 30,
    }


@pytest.fixture
def client(
    base_config: dict[str, Any],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> AppleScriptClient:
    """Create AppleScriptClient instance."""
    return AppleScriptClient(
        config=base_config,
        analytics=MagicMock(),
        console_logger=console_logger,
        error_logger=error_logger,
    )


class TestInitializationErrors:
    """Tests for initialization error paths."""

    def test_init_logs_critical_when_scripts_dir_missing(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test logs critical error when apple_scripts_dir is missing."""
        config: dict[str, Any] = {}  # Missing apple_scripts_dir
        client = AppleScriptClient(
            config=config,
            analytics=MagicMock(),
            console_logger=console_logger,
            error_logger=error_logger,
        )
        assert client.apple_scripts_dir is None

    @pytest.mark.asyncio
    async def test_initialize_raises_when_scripts_dir_none(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test initialize() raises ValueError when scripts dir is None."""
        config: dict[str, Any] = {}  # Missing apple_scripts_dir
        client = AppleScriptClient(
            config=config,
            analytics=MagicMock(),
            console_logger=console_logger,
            error_logger=error_logger,
        )
        with pytest.raises(ValueError, match="AppleScript directory is not set"):
            await client.initialize()

    @pytest.mark.asyncio
    async def test_initialize_raises_when_dir_not_exists(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test initialize() raises FileNotFoundError for non-existent dir."""
        config = {"apple_scripts_dir": "/nonexistent/path/to/scripts"}
        client = AppleScriptClient(
            config=config,
            analytics=MagicMock(),
            console_logger=console_logger,
            error_logger=error_logger,
        )
        with pytest.raises(FileNotFoundError, match="not accessible"):
            await client.initialize()

    @pytest.mark.asyncio
    async def test_initialize_warns_missing_required_scripts(
        self,
        tmp_path: Path,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test initialize() warns about missing required scripts."""
        scripts_dir = tmp_path / "applescripts"
        scripts_dir.mkdir()
        # Don't create the required scripts

        config = {
            "apple_scripts_dir": str(scripts_dir),
            "apple_script_concurrency": 2,
        }
        client = AppleScriptClient(
            config=config,
            analytics=MagicMock(),
            console_logger=console_logger,
            error_logger=error_logger,
        )
        # Should not raise, just warn
        await client.initialize()
        assert client.semaphore is not None

    @pytest.mark.asyncio
    async def test_initialize_raises_on_invalid_concurrency(
        self,
        tmp_path: Path,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test initialize() raises ValueError for invalid concurrency."""
        scripts_dir = tmp_path / "applescripts"
        scripts_dir.mkdir()

        config = {
            "apple_scripts_dir": str(scripts_dir),
            "apple_script_concurrency": 0,  # Invalid - must be positive
        }
        client = AppleScriptClient(
            config=config,
            analytics=MagicMock(),
            console_logger=console_logger,
            error_logger=error_logger,
        )
        with pytest.raises(ValueError, match="Invalid concurrency limit"):
            await client.initialize()

    @pytest.mark.asyncio
    async def test_initialize_skips_if_already_initialized(self, client: AppleScriptClient) -> None:
        """Test initialize() skips semaphore creation if already initialized."""
        await client.initialize()
        original_semaphore = client.semaphore
        # Call again
        await client.initialize()
        assert client.semaphore is original_semaphore


class TestBuildCommandWithArgs:
    """Tests for _build_command_with_args method."""

    def test_accepts_shell_metacharacters(self, client: AppleScriptClient) -> None:
        """Test accepts shell metacharacters - safe with create_subprocess_exec."""
        result = client._build_command_with_args("/path/script.scpt", ["Seek & Destroy", "Rock & Roll"])
        assert result == ["osascript", "/path/script.scpt", "Seek & Destroy", "Rock & Roll"]

    def test_returns_command_for_safe_args(self, client: AppleScriptClient) -> None:
        """Test returns command list for safe arguments."""
        result = client._build_command_with_args("/path/script.scpt", ["arg1", "arg2"])
        assert result == ["osascript", "/path/script.scpt", "arg1", "arg2"]

    def test_returns_command_with_no_args(self, client: AppleScriptClient) -> None:
        """Test returns command when no arguments provided."""
        result = client._build_command_with_args("/path/script.scpt", None)
        assert result == ["osascript", "/path/script.scpt"]


class TestLogScriptResult:
    """Tests for _log_script_result method."""

    def test_logs_warning_for_none_result(self, client: AppleScriptClient) -> None:
        """Test logs warning when result is None."""
        # Should not raise
        client._log_script_result(None)

    def test_logs_success_for_result(self, client: AppleScriptClient) -> None:
        """Test logs success for valid result."""
        client._log_script_result("Success output")


class TestRunScript:
    """Tests for run_script method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_scripts_dir_none(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test returns None when apple_scripts_dir is None."""
        config: dict[str, Any] = {}
        client = AppleScriptClient(
            config=config,
            analytics=MagicMock(),
            console_logger=console_logger,
            error_logger=error_logger,
        )
        result = await client.run_script("test.scpt")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_script_path(self, client: AppleScriptClient) -> None:
        """Test returns None for path traversal attempt."""
        await client.initialize()
        # Path traversal attempt
        result = await client.run_script("../../../etc/passwd")
        assert result is None

    @pytest.mark.asyncio
    async def test_accepts_shell_metacharacters_in_args(self, client: AppleScriptClient) -> None:
        """Test accepts shell metacharacters - safe with create_subprocess_exec."""
        await client.initialize()
        with patch.object(client.executor, "run_osascript", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Success"
            result = await client.run_script(
                "update_property.applescript",
                arguments=["track_id", "name", "Seek & Destroy"],
            )
            assert result == "Success"
            # Verify the & character was passed through
            call_args = mock_run.call_args[0][0]
            assert "Seek & Destroy" in call_args

    @pytest.mark.asyncio
    async def test_logs_context_info_when_provided(self, client: AppleScriptClient) -> None:
        """Test logs contextual information when provided."""
        await client.initialize()
        with patch.object(client.executor, "run_osascript", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Success"
            result = await client.run_script(
                "update_property.applescript",
                context_artist="Test Artist",
                context_album="Test Album",
                context_track="Test Track",
            )
            assert result == "Success"

    @pytest.mark.asyncio
    async def test_raises_timeout_error(self, client: AppleScriptClient) -> None:
        """Test raises TimeoutError when script times out."""
        await client.initialize()
        with patch.object(client.executor, "run_osascript", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = TimeoutError("Timed out")
            with pytest.raises(TimeoutError):
                await client.run_script("update_property.applescript", timeout=1)

    @pytest.mark.asyncio
    async def test_raises_os_error(self, client: AppleScriptClient) -> None:
        """Test raises OSError when script fails."""
        await client.initialize()
        with patch.object(client.executor, "run_osascript", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = OSError("Script failed")
            with pytest.raises(OSError, match="Script failed"):
                await client.run_script("update_property.applescript")


class TestInitializeOSError:
    """Tests for initialize() OSError handling when listing directory."""

    @pytest.mark.asyncio
    async def test_initialize_handles_os_error_listing_directory(
        self,
        tmp_path: Path,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Test initialize() handles OSError when listing directory."""
        scripts_dir = tmp_path / "applescripts"
        scripts_dir.mkdir()
        # Create the required scripts so we pass the main validation
        (scripts_dir / "update_property.applescript").write_text("-- test")
        (scripts_dir / "fetch_tracks.applescript").write_bytes(b"-- test")

        config = {
            "apple_scripts_dir": str(scripts_dir),
            "apple_script_concurrency": 2,
        }
        client = AppleScriptClient(
            config=config,
            analytics=MagicMock(),
            console_logger=console_logger,
            error_logger=error_logger,
        )

        # Mock Path.iterdir to raise OSError
        with patch.object(Path, "iterdir", side_effect=OSError("Permission denied")):
            # Should not raise, just warn
            await client.initialize()
            assert client.semaphore is not None


class TestRunScriptFileAccessValidation:
    """Tests for run_script file access validation."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_access_validation_fails(self, client: AppleScriptClient) -> None:
        """Test returns None when file_validator.validate_script_file_access returns False."""
        await client.initialize()
        with (
            patch.object(client.file_validator, "validate_script_path", return_value=True),
            patch.object(client.file_validator, "validate_script_file_access", return_value=False),
        ):
            result = await client.run_script("update_property.applescript")
            assert result is None


class TestFetchTracksByIds:
    """Tests for fetch_tracks_by_ids method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_ids(self, client: AppleScriptClient) -> None:
        """Test returns empty list when track_ids is empty."""
        await client.initialize()
        result = await client.fetch_tracks_by_ids([])
        assert result == []

    @pytest.mark.asyncio
    async def test_fetches_tracks_in_batches(self, client: AppleScriptClient) -> None:
        """Test fetches tracks in batches and combines results."""
        await client.initialize()

        # Create mock output that matches expected format
        field_sep = "\x1e"
        line_sep = "\x1d"
        track_data = (
            f"123{field_sep}Track Name{field_sep}Artist{field_sep}Album Artist{field_sep}"
            f"Album{field_sep}Rock{field_sep}2023-01-01{field_sep}subscription{field_sep}"
            f"2020{field_sep}2020{field_sep}{line_sep}"
        )

        with patch.object(client, "run_script", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = track_data
            result = await client.fetch_tracks_by_ids(["123", "456"], batch_size=1)
            assert len(result) == 2  # Called twice with batch_size=1
            assert mock_run.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_no_tracks_found_response(self, client: AppleScriptClient) -> None:
        """Test handles NO_TRACKS_FOUND response from script."""
        await client.initialize()

        with patch.object(client, "run_script", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "NO_TRACKS_FOUND"
            result = await client.fetch_tracks_by_ids(["123"])
            assert result == []

    @pytest.mark.asyncio
    async def test_handles_empty_response(self, client: AppleScriptClient) -> None:
        """Test handles empty response from script."""
        await client.initialize()

        with patch.object(client, "run_script", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ""
            result = await client.fetch_tracks_by_ids(["123"])
            assert result == []

    @pytest.mark.asyncio
    async def test_uses_default_timeout(self, client: AppleScriptClient) -> None:
        """Test uses default timeout when not specified."""
        await client.initialize()

        with patch.object(client, "run_script", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ""
            await client.fetch_tracks_by_ids(["123"])
            # Check that timeout was passed
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert "timeout" in call_kwargs.kwargs


class TestParseTrackOutput:
    """Tests for _parse_track_output static method."""

    def test_parses_valid_track_output(self) -> None:
        """Test parses valid track output with all fields."""
        field_sep = "\x1e"
        line_sep = "\x1d"
        raw_output = (
            f"123{field_sep}Track Name{field_sep}Artist{field_sep}Album Artist{field_sep}"
            f"Album{field_sep}Rock{field_sep}2023-01-01{field_sep}subscription{field_sep}"
            f"2020{field_sep}2020{field_sep}2021{line_sep}"
        )

        result = self._assert_parse_tracks(raw_output, expected_count=1, first_id="123")
        assert result[0]["name"] == "Track Name"
        assert result[0]["artist"] == "Artist"
        assert result[0]["album_artist"] == "Album Artist"
        assert result[0]["album"] == "Album"
        assert result[0]["genre"] == "Rock"
        assert result[0]["date_added"] == "2023-01-01"
        assert result[0]["track_status"] == "subscription"
        assert result[0]["year"] == "2020"
        assert result[0]["release_year"] == "2020"
        assert result[0]["year_set_by_mgu"] == "2021"

    def test_parses_multiple_tracks(self) -> None:
        """Test parses multiple tracks separated by line separator."""
        field_sep = "\x1e"
        line_sep = "\x1d"
        raw_output = (
            f"1{field_sep}T1{field_sep}A1{field_sep}AA1{field_sep}Al1{field_sep}G1{field_sep}"
            f"D1{field_sep}S1{field_sep}Y1{field_sep}RY1{field_sep}NY1{line_sep}"
            f"2{field_sep}T2{field_sep}A2{field_sep}AA2{field_sep}Al2{field_sep}G2{field_sep}"
            f"D2{field_sep}S2{field_sep}Y2{field_sep}RY2{field_sep}NY2{line_sep}"
        )

        result = self._assert_parse_tracks(raw_output, expected_count=2, first_id="1")
        assert result[1]["id"] == "2"

    def test_skips_empty_lines(self) -> None:
        """Test skips empty lines in output."""
        field_sep = "\x1e"
        line_sep = "\x1d"
        raw_output = (
            f"{line_sep}"  # Empty line
            f"123{field_sep}Track{field_sep}A{field_sep}AA{field_sep}Al{field_sep}G{field_sep}"
            f"D{field_sep}S{field_sep}Y{field_sep}RY{field_sep}NY{line_sep}"
            f"   {line_sep}"  # Whitespace only
        )

        self._assert_parse_tracks(raw_output, expected_count=1, first_id="123")

    def test_skips_lines_with_insufficient_fields(self) -> None:
        """Test skips lines with fewer than 11 fields."""
        field_sep = "\x1e"
        line_sep = "\x1d"
        raw_output = (
            f"123{field_sep}Track{field_sep}Artist{line_sep}"  # Only 3 fields
            f"456{field_sep}T{field_sep}A{field_sep}AA{field_sep}Al{field_sep}G{field_sep}"
            f"D{field_sep}S{field_sep}Y{field_sep}RY{field_sep}NY{line_sep}"
        )

        self._assert_parse_tracks(raw_output, expected_count=1, first_id="456")

    @staticmethod
    def _assert_parse_tracks(
        raw_output: str,
        expected_count: int,
        first_id: str,
    ) -> list[dict[str, str]]:
        """Parse track output and assert expected count and first track ID."""
        result = AppleScriptClient._parse_track_output(raw_output)
        assert len(result) == expected_count
        assert result[0]["id"] == first_id
        return result
