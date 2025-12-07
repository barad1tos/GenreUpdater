"""Tests for CLI module."""

from __future__ import annotations

import argparse
from io import StringIO
from unittest.mock import patch

import pytest

from app.cli import CLI

# Test constants
TEST_PASSWORD = "test_secret_123"  # noqa: S105


@pytest.fixture
def cli() -> CLI:
    """Create a CLI instance."""
    return CLI()


class TestCLIInit:
    """Tests for CLI initialization."""

    def test_creates_parser(self) -> None:
        """Should create argument parser on init."""
        cli = CLI()
        assert cli.parser is not None
        assert isinstance(cli.parser, argparse.ArgumentParser)

    def test_parser_prog_name(self, cli: CLI) -> None:
        """Should set correct program name."""
        assert cli.parser.prog == "python main.py"

    def test_parser_description(self, cli: CLI) -> None:
        """Should set correct description."""
        assert "Music Genre Updater" in str(cli.parser.description)


class TestGlobalArguments:
    """Tests for global CLI arguments."""

    def test_force_flag(self, cli: CLI) -> None:
        """Should parse --force flag."""
        args = cli.parse_args(["--force"])
        assert args.force is True

    def test_force_flag_default(self, cli: CLI) -> None:
        """Should default force to False."""
        args = cli.parse_args([])
        assert args.force is False

    def test_dry_run_flag(self, cli: CLI) -> None:
        """Should parse --dry-run flag."""
        args = cli.parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_dry_run_default(self, cli: CLI) -> None:
        """Should default dry_run to False."""
        args = cli.parse_args([])
        assert args.dry_run is False

    def test_test_mode_flag(self, cli: CLI) -> None:
        """Should parse --test-mode flag."""
        args = cli.parse_args(["--test-mode"])
        assert args.test_mode is True

    def test_test_mode_default(self, cli: CLI) -> None:
        """Should default test_mode to False."""
        args = cli.parse_args([])
        assert args.test_mode is False

    def test_verbose_flag_long(self, cli: CLI) -> None:
        """Should parse --verbose flag."""
        args = cli.parse_args(["--verbose"])
        assert args.verbose is True

    def test_verbose_flag_short(self, cli: CLI) -> None:
        """Should parse -v flag."""
        args = cli.parse_args(["-v"])
        assert args.verbose is True

    def test_verbose_default(self, cli: CLI) -> None:
        """Should default verbose to False."""
        args = cli.parse_args([])
        assert args.verbose is False

    def test_quiet_flag_long(self, cli: CLI) -> None:
        """Should parse --quiet flag."""
        args = cli.parse_args(["--quiet"])
        assert args.quiet is True

    def test_quiet_flag_short(self, cli: CLI) -> None:
        """Should parse -q flag."""
        args = cli.parse_args(["-q"])
        assert args.quiet is True

    def test_quiet_default(self, cli: CLI) -> None:
        """Should default quiet to False."""
        args = cli.parse_args([])
        assert args.quiet is False

    def test_config_option(self, cli: CLI) -> None:
        """Should parse --config option."""
        args = cli.parse_args(["--config", "my-config.yaml"])
        assert args.config == "my-config.yaml"

    def test_config_default(self, cli: CLI) -> None:
        """Should default config to None."""
        args = cli.parse_args([])
        assert args.config is None

    def test_multiple_global_flags(self, cli: CLI) -> None:
        """Should parse multiple global flags."""
        args = cli.parse_args(["--force", "--dry-run", "--verbose", "--config", "test.yaml"])
        assert args.force is True
        assert args.dry_run is True
        assert args.verbose is True
        assert args.config == "test.yaml"

    def test_no_command_default(self, cli: CLI) -> None:
        """Should have None command when not specified."""
        args = cli.parse_args([])
        assert args.command is None


class TestCleanArtistCommand:
    """Tests for clean_artist command."""

    def test_command_recognized(self, cli: CLI) -> None:
        """Should recognize clean_artist command."""
        args = cli.parse_args(["clean_artist", "--artist", "Pink Floyd"])
        assert args.command == "clean_artist"

    def test_alias_recognized(self, cli: CLI) -> None:
        """Should recognize clean alias."""
        args = cli.parse_args(["clean", "--artist", "Pink Floyd"])
        # Argparse stores the actual alias used, not the primary command name
        assert args.command == "clean"
        assert args.artist == "Pink Floyd"

    def test_artist_argument_required(self, cli: CLI) -> None:
        """Should require --artist argument."""
        with pytest.raises(SystemExit):
            cli.parse_args(["clean_artist"])

    def test_artist_value_parsed(self, cli: CLI) -> None:
        """Should parse artist value."""
        args = cli.parse_args(["clean_artist", "--artist", "Pink Floyd"])
        assert args.artist == "Pink Floyd"

    def test_with_global_flags(self, cli: CLI) -> None:
        """Should combine with global flags."""
        args = cli.parse_args(["--dry-run", "clean_artist", "--artist", "Test"])
        assert args.dry_run is True
        assert args.command == "clean_artist"
        assert args.artist == "Test"


class TestUpdateYearsCommand:
    """Tests for update_years command."""

    def test_command_recognized(self, cli: CLI) -> None:
        """Should recognize update_years command."""
        args = cli.parse_args(["update_years"])
        assert args.command == "update_years"

    def test_alias_recognized(self, cli: CLI) -> None:
        """Should recognize years alias."""
        args = cli.parse_args(["years"])
        # Argparse stores the actual alias used
        assert args.command == "years"

    def test_artist_optional(self, cli: CLI) -> None:
        """Should allow command without artist."""
        args = cli.parse_args(["update_years"])
        assert args.artist is None

    def test_artist_value_parsed(self, cli: CLI) -> None:
        """Should parse optional artist value."""
        args = cli.parse_args(["update_years", "--artist", "The Beatles"])
        assert args.artist == "The Beatles"

    def test_with_force_flag(self, cli: CLI) -> None:
        """Should combine with force flag."""
        args = cli.parse_args(["--force", "update_years"])
        assert args.force is True
        assert args.command == "update_years"


class TestRevertYearsCommand:
    """Tests for revert_years command."""

    def test_command_recognized(self, cli: CLI) -> None:
        """Should recognize revert_years command."""
        args = cli.parse_args(["revert_years", "--artist", "Test"])
        assert args.command == "revert_years"

    def test_alias_recognized(self, cli: CLI) -> None:
        """Should recognize revert alias."""
        args = cli.parse_args(["revert", "--artist", "Test"])
        # Argparse stores the actual alias used
        assert args.command == "revert"

    def test_artist_required(self, cli: CLI) -> None:
        """Should require --artist argument."""
        with pytest.raises(SystemExit):
            cli.parse_args(["revert_years"])

    def test_artist_value_parsed(self, cli: CLI) -> None:
        """Should parse artist value."""
        args = cli.parse_args(["revert_years", "--artist", "Led Zeppelin"])
        assert args.artist == "Led Zeppelin"

    def test_album_optional(self, cli: CLI) -> None:
        """Should allow optional --album argument."""
        args = cli.parse_args(["revert_years", "--artist", "Test"])
        assert args.album is None

    def test_album_value_parsed(self, cli: CLI) -> None:
        """Should parse album value."""
        args = cli.parse_args(["revert_years", "--artist", "Test", "--album", "Album Name"])
        assert args.album == "Album Name"

    def test_backup_csv_optional(self, cli: CLI) -> None:
        """Should allow optional --backup-csv argument."""
        args = cli.parse_args(["revert_years", "--artist", "Test"])
        assert args.backup_csv is None

    def test_backup_csv_value_parsed(self, cli: CLI) -> None:
        """Should parse backup-csv value."""
        args = cli.parse_args(["revert_years", "--artist", "Test", "--backup-csv", "/path/to/backup.csv"])
        assert args.backup_csv == "/path/to/backup.csv"

    def test_all_arguments(self, cli: CLI) -> None:
        """Should parse all revert_years arguments."""
        args = cli.parse_args(
            [
                "revert_years",
                "--artist",
                "Artist",
                "--album",
                "Album",
                "--backup-csv",
                "/path.csv",
            ]
        )
        assert args.artist == "Artist"
        assert args.album == "Album"
        assert args.backup_csv == "/path.csv"


class TestVerifyDatabaseCommand:
    """Tests for verify_database command."""

    def test_command_recognized(self, cli: CLI) -> None:
        """Should recognize verify_database command."""
        args = cli.parse_args(["verify_database"])
        assert args.command == "verify_database"

    def test_alias_recognized(self, cli: CLI) -> None:
        """Should recognize verify-db alias."""
        args = cli.parse_args(["verify-db"])
        # Argparse stores the actual alias used
        assert args.command == "verify-db"

    def test_no_additional_args(self, cli: CLI) -> None:
        """Should work without additional arguments."""
        args = cli.parse_args(["verify_database"])
        assert args.command == "verify_database"


class TestVerifyPendingCommand:
    """Tests for verify_pending command."""

    def test_command_recognized(self, cli: CLI) -> None:
        """Should recognize verify_pending command."""
        args = cli.parse_args(["verify_pending"])
        assert args.command == "verify_pending"

    def test_alias_recognized(self, cli: CLI) -> None:
        """Should recognize pending alias."""
        args = cli.parse_args(["pending"])
        # Argparse stores the actual alias used
        assert args.command == "pending"

    def test_no_additional_args(self, cli: CLI) -> None:
        """Should work without additional arguments."""
        args = cli.parse_args(["verify_pending"])
        assert args.command == "verify_pending"


class TestBatchCommand:
    """Tests for batch command."""

    def test_command_recognized(self, cli: CLI) -> None:
        """Should recognize batch command."""
        args = cli.parse_args(["batch", "--file", "artists.txt"])
        assert args.command == "batch"

    def test_file_required(self, cli: CLI) -> None:
        """Should require --file argument."""
        with pytest.raises(SystemExit):
            cli.parse_args(["batch"])

    def test_file_value_parsed(self, cli: CLI) -> None:
        """Should parse file value."""
        args = cli.parse_args(["batch", "--file", "/path/to/artists.txt"])
        assert args.file == "/path/to/artists.txt"

    def test_operation_default(self, cli: CLI) -> None:
        """Should default operation to full."""
        args = cli.parse_args(["batch", "--file", "file.txt"])
        assert args.operation == "full"

    def test_operation_clean(self, cli: CLI) -> None:
        """Should accept clean operation."""
        args = cli.parse_args(["batch", "--file", "f.txt", "--operation", "clean"])
        assert args.operation == "clean"

    def test_operation_years(self, cli: CLI) -> None:
        """Should accept years operation."""
        args = cli.parse_args(["batch", "--file", "f.txt", "--operation", "years"])
        assert args.operation == "years"

    def test_operation_full(self, cli: CLI) -> None:
        """Should accept full operation."""
        args = cli.parse_args(["batch", "--file", "f.txt", "--operation", "full"])
        assert args.operation == "full"

    def test_operation_invalid(self, cli: CLI) -> None:
        """Should reject invalid operation."""
        with pytest.raises(SystemExit):
            cli.parse_args(["batch", "--file", "f.txt", "--operation", "invalid"])


class TestRotateKeysCommand:
    """Tests for rotate_keys command."""

    def test_command_recognized(self, cli: CLI) -> None:
        """Should recognize rotate_keys command."""
        args = cli.parse_args(["rotate_keys"])
        assert args.command == "rotate_keys"

    def test_alias_recognized(self, cli: CLI) -> None:
        """Should recognize rotate-keys alias."""
        args = cli.parse_args(["rotate-keys"])
        # Argparse stores the actual alias used
        assert args.command == "rotate-keys"

    def test_no_args_required(self, cli: CLI) -> None:
        """Should work without arguments."""
        args = cli.parse_args(["rotate_keys"])
        assert args.command == "rotate_keys"

    def test_new_password_optional(self, cli: CLI) -> None:
        """Should default new_password to None."""
        args = cli.parse_args(["rotate_keys"])
        assert args.new_password is None

    def test_new_password_value_parsed(self, cli: CLI) -> None:
        """Should parse new-password value."""
        args = cli.parse_args(["rotate_keys", "--new-password", TEST_PASSWORD])
        assert args.new_password == TEST_PASSWORD

    def test_no_backup_flag(self, cli: CLI) -> None:
        """Should parse --no-backup flag."""
        args = cli.parse_args(["rotate_keys", "--no-backup"])
        assert args.no_backup is True

    def test_no_backup_default(self, cli: CLI) -> None:
        """Should default no_backup to False."""
        args = cli.parse_args(["rotate_keys"])
        assert args.no_backup is False

    def test_all_arguments(self, cli: CLI) -> None:
        """Should parse all rotate_keys arguments."""
        args = cli.parse_args(["rotate_keys", "--new-password", TEST_PASSWORD, "--no-backup"])
        assert args.new_password == TEST_PASSWORD
        assert args.no_backup is True


@pytest.fixture
def help_output(cli: CLI) -> str:
    """Capture CLI help output to string."""
    with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        cli.print_help()
        output: str = mock_stdout.getvalue()
    return output


class TestPrintHelp:
    """Tests for print_help method."""

    def test_prints_help(self, help_output: str) -> None:
        """Should print help message."""
        assert "Music Genre Updater" in help_output
        assert "--force" in help_output
        assert "--dry-run" in help_output

    def test_help_includes_examples(self, help_output: str) -> None:
        """Should include examples in help."""
        assert "Examples:" in help_output
        assert "clean_artist" in help_output


class TestParseArgs:
    """Tests for parse_args method."""

    def test_returns_namespace(self, cli: CLI) -> None:
        """Should return argparse.Namespace."""
        result = cli.parse_args([])
        assert isinstance(result, argparse.Namespace)

    def test_with_none_uses_sys_argv(self, cli: CLI) -> None:
        """Should use sys.argv when args is None."""
        with patch("sys.argv", ["main.py", "--force"]):
            result = cli.parse_args()
            assert result.force is True

    def test_with_empty_list(self, cli: CLI) -> None:
        """Should handle empty args list."""
        result = cli.parse_args([])
        assert result.command is None
        assert result.force is False
