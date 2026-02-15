"""Tests for AppleScriptFileValidator covering security validation branches."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.apple.file_validator import AppleScriptFileValidator


@pytest.fixture
def scripts_dir(tmp_path: Path) -> Path:
    """Create a temporary scripts directory."""
    directory = tmp_path / "applescripts"
    directory.mkdir()
    return directory


@pytest.fixture
def validator(scripts_dir: Path) -> AppleScriptFileValidator:
    """Create a validator with mock loggers."""
    return AppleScriptFileValidator(
        apple_scripts_directory=str(scripts_dir),
        error_logger=MagicMock(spec=logging.Logger),
        console_logger=MagicMock(spec=logging.Logger),
    )


class TestValidateScriptPathOutsideDirectory:
    """Tests for validate_script_path with paths outside allowed directory."""

    def test_path_outside_allowed_directory_returns_false(
        self,
        validator: AppleScriptFileValidator,
    ) -> None:
        """Test that a path outside the allowed directory returns False and logs error."""
        result = validator.validate_script_path("/etc/passwd")

        assert result is False
        validator.error_logger.error.assert_called_once()
        assert "outside allowed directory" in validator.error_logger.error.call_args[0][0]

    def test_sibling_directory_path_returns_false(
        self,
        validator: AppleScriptFileValidator,
        tmp_path: Path,
    ) -> None:
        """Test that a sibling directory path returns False."""
        sibling = tmp_path / "other_dir"
        sibling.mkdir()
        script = sibling / "evil.scpt"
        script.write_text("-- evil")

        result = validator.validate_script_path(str(script))

        assert result is False
        validator.error_logger.error.assert_called_once()


class TestValidateScriptFileAccessResolvedPathEscape:
    """Tests for validate_script_file_access when resolved path escapes allowed dir."""

    def test_resolved_path_outside_allowed_dir_returns_false(
        self,
        tmp_path: Path,
    ) -> None:
        """Test resolved path that escapes allowed directory returns False.

        Uses two separate real directories so resolve(strict=True) succeeds
        but the resolved path is outside the allowed scripts directory.
        """
        scripts_dir = tmp_path / "allowed_scripts"
        scripts_dir.mkdir()

        # Create file in a different directory (outside allowed)
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "escaped.scpt"
        outside_file.write_text("-- escaped")

        error_logger = MagicMock(spec=logging.Logger)
        validator = AppleScriptFileValidator(
            apple_scripts_directory=str(scripts_dir),
            error_logger=error_logger,
            console_logger=MagicMock(spec=logging.Logger),
        )

        result = validator.validate_script_file_access(str(outside_file))

        assert result is False
        error_calls = error_logger.error.call_args_list
        assert any("Resolved path escapes allowed directory" in str(call) for call in error_calls)

    def test_symlink_to_outside_returns_false(
        self,
        validator: AppleScriptFileValidator,
        scripts_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Test symlink pointing outside allowed directory returns False."""
        outside_file = tmp_path / "outside.scpt"
        outside_file.write_text("-- outside")

        symlink = scripts_dir / "link.scpt"
        symlink.symlink_to(outside_file)

        result = validator.validate_script_file_access(str(symlink))

        assert result is False
        validator.error_logger.error.assert_called_once()
        assert "Symlinks not allowed" in validator.error_logger.error.call_args[0][0]


class TestValidateScriptFileAccessDirectoryListingOSError:
    """Tests for OSError branch when listing directory contents for debugging."""

    def test_oserror_on_directory_listing_logs_debug(
        self,
        validator: AppleScriptFileValidator,
        scripts_dir: Path,
    ) -> None:
        """Test OSError during directory listing logs debug message.

        Creates a real file so resolve(strict=True) succeeds, then mocks
        is_file to return False so we enter the directory listing branch,
        then mocks iterdir to raise OSError.
        """
        real_file = scripts_dir / "exists.scpt"
        real_file.write_text("-- exists")

        with (
            patch.object(Path, "is_file", return_value=False),
            patch.object(Path, "iterdir", side_effect=OSError("Permission denied")),
        ):
            result = validator.validate_script_file_access(str(real_file))

        assert result is False
        debug_calls = validator.console_logger.debug.call_args_list
        assert any("Could not list directory contents" in str(call) for call in debug_calls)
