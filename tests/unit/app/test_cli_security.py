"""Tests for CLI argument security boundaries.

Verifies that CLI parsing handles adversarial inputs without crashing
and preserves values exactly as provided for downstream validation.
"""

from __future__ import annotations

import pytest

from app.cli import CLI


@pytest.mark.unit
class TestCLIArgumentSecurity:
    """CLI parsing handles adversarial inputs without crashing."""

    def test_artist_with_shell_metacharacters(self) -> None:
        """Shell metacharacters in --artist are preserved as-is."""
        cli = CLI()
        args = cli.parse_args(["clean_artist", "--artist", 'Artist"; rm -rf /'])
        assert args.artist == 'Artist"; rm -rf /'

    def test_artist_with_command_substitution(self) -> None:
        """Command substitution in --artist is preserved as-is."""
        cli = CLI()
        args = cli.parse_args(["clean_artist", "--artist", "$(whoami)"])
        assert args.artist == "$(whoami)"

    def test_artist_with_backtick_execution(self) -> None:
        """Backtick execution in --artist is preserved as-is."""
        cli = CLI()
        args = cli.parse_args(["clean_artist", "--artist", "`id`"])
        assert args.artist == "`id`"

    def test_artist_with_pipe_injection(self) -> None:
        """Pipe characters in --artist are preserved as-is."""
        cli = CLI()
        args = cli.parse_args(["clean_artist", "--artist", "Artist | tee /tmp/log"])
        assert args.artist == "Artist | tee /tmp/log"

    def test_album_with_path_traversal_attempt(self) -> None:
        """Path traversal in --album is preserved as-is (validation is downstream)."""
        cli = CLI()
        args = cli.parse_args(["revert_years", "--artist", "A", "--album", "../../../etc/passwd"])
        assert args.album == "../../../etc/passwd"

    def test_config_path_traversal_attempt(self) -> None:
        """Path traversal in --config is preserved as-is."""
        cli = CLI()
        args = cli.parse_args(["--config", "../../../etc/passwd"])
        assert args.config == "../../../etc/passwd"

    def test_batch_file_path_traversal(self) -> None:
        """Path traversal in batch --file is preserved as-is."""
        cli = CLI()
        args = cli.parse_args(["batch", "--file", "/etc/passwd"])
        assert args.file == "/etc/passwd"

    def test_extremely_long_artist_name(self) -> None:
        """Very long artist name (10000 chars) doesn't crash argparse."""
        cli = CLI()
        long_name = "A" * 10000
        args = cli.parse_args(["clean_artist", "--artist", long_name])
        assert args.artist == long_name

    def test_empty_string_artist(self) -> None:
        """Empty string as artist name is accepted by argparse."""
        cli = CLI()
        args = cli.parse_args(["clean_artist", "--artist", ""])
        assert args.artist == ""

    def test_unicode_artist_name(self) -> None:
        """Unicode characters in artist name are preserved."""
        cli = CLI()
        args = cli.parse_args(["clean_artist", "--artist", "Björk"])
        assert args.artist == "Björk"

    def test_null_bytes_in_artist(self) -> None:
        """Null bytes in artist name are preserved by argparse."""
        cli = CLI()
        args = cli.parse_args(["clean_artist", "--artist", "Art\x00ist"])
        assert args.artist == "Art\x00ist"

    def test_newline_injection_in_artist(self) -> None:
        """Newlines in artist name don't cause argument injection."""
        cli = CLI()
        args = cli.parse_args(["clean_artist", "--artist", "Artist\n--force"])
        assert args.artist == "Artist\n--force"
        # --force is a global flag, so it exists but should be False (default)
        assert args.force is False

    def test_threshold_with_negative_value(self) -> None:
        """Negative threshold is accepted by argparse (no bounds)."""
        cli = CLI()
        args = cli.parse_args(["restore_release_years", "--threshold=-1"])
        assert args.threshold == -1

    def test_threshold_with_very_large_value(self) -> None:
        """Very large threshold is accepted by argparse."""
        cli = CLI()
        args = cli.parse_args(["restore_release_years", "--threshold", "999999"])
        assert args.threshold == 999999
