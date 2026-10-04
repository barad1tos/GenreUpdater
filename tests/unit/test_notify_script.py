"""Tests for launchctl/bin/notify.sh, the daemon's macOS notification helper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

NOTIFY_SCRIPT = Path(__file__).resolve().parents[2] / "launchctl" / "bin" / "notify.sh"


def _run_with_recording_osascript(tmp_path: Path, *arguments: str) -> list[str]:
    """Run notify.sh with a fake osascript that records its argv, one argument per line."""
    recorded = tmp_path / "argv.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_osascript = fake_bin / "osascript"
    fake_osascript.write_text(f'#!/bin/bash\nprintf "%s\\0" "$@" > "{recorded}"\n', encoding="utf-8")
    fake_osascript.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    subprocess.run(["/bin/bash", str(NOTIFY_SCRIPT), *arguments], check=True, env=environment)
    return recorded.read_text(encoding="utf-8").split("\0")[:-1]


def test_error_text_with_quotes_reaches_osascript_as_data(tmp_path: Path) -> None:
    """Quotes and backslashes in a log snippet must not become part of the AppleScript source."""
    message = 'Exit code 1: KeyError: "genre" \\ path\'s end'

    argv = _run_with_recording_osascript(tmp_path, "Genre Updater Error", message, "Basso")

    script = argv[: argv.index("--")]
    assert message not in " ".join(script)
    assert argv[argv.index("--") + 1 :] == ["Genre Updater Error", message, "Basso"]


def test_defaults_apply_when_arguments_are_missing(tmp_path: Path) -> None:
    argv = _run_with_recording_osascript(tmp_path)

    assert argv[argv.index("--") + 1 :] == ["Genre Updater", "No message provided", "Basso"]
