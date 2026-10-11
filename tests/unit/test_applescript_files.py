"""The dialog and notification scripts exist, take their text as arguments and compile where osacompile exists."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.apple_script_names import ASK_YEAR_DRIFT, FETCH_TRACK_IDS, NOTIFY_YEAR_CHANGES

SCRIPTS = Path(__file__).resolve().parents[2] / "applescripts"
OSACOMPILE = shutil.which("osacompile")


@pytest.mark.parametrize("name", [ASK_YEAR_DRIFT, NOTIFY_YEAR_CHANGES])
def test_user_facing_scripts_take_their_text_from_argv(name: str) -> None:
    source = (SCRIPTS / name).read_text(encoding="utf-8")

    assert "on run argv" in source
    assert "item 1 of argv" in source


def test_the_dialog_gives_up_and_names_its_answers() -> None:
    source = (SCRIPTS / ASK_YEAR_DRIFT).read_text(encoding="utf-8")

    assert "giving up after" in source
    assert all(answer in source for answer in ('"keep"', '"fix"', '"timeout"'))


@pytest.mark.skipif(OSACOMPILE is None, reason="osacompile is macOS only")
@pytest.mark.parametrize("name", [ASK_YEAR_DRIFT, NOTIFY_YEAR_CHANGES, FETCH_TRACK_IDS])
def test_scripts_compile(name: str, tmp_path: Path) -> None:
    assert OSACOMPILE is not None
    result = subprocess.run([OSACOMPILE, "-o", str(tmp_path / "out.scpt"), str(SCRIPTS / name)], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
