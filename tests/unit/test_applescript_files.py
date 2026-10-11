"""The dialog and notification scripts exist, take their text as arguments and compile where osacompile exists."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from core.apple_script_names import ASK_YEAR_DRIFT, FETCH_TRACK_IDS, NOTIFY_YEAR_CHANGES
from core.tracks.track_delta import FIELD_SEPARATOR, LINE_SEPARATOR

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


def test_the_dialog_reads_its_arguments_in_the_order_the_decider_sends_them() -> None:
    """YearDriftDecider.decide sends [artist, album, old year, new year, providers' year, seconds]; a swap would mislabel the buttons."""
    source = (SCRIPTS / ASK_YEAR_DRIFT).read_text(encoding="utf-8")

    positions = {name: int(index) for name, index in re.findall(r"set (\w+) to \(?item (\d) of argv", source)}

    assert positions == {"artistName": 1, "albumName": 2, "oldYear": 3, "newYear": 4, "providerYear": 5, "giveUpAfter": 6}
    assert 'set keepButton to "Зберегти " & newYear' in source
    assert 'set fixButton to "Виправити на " & providerYear' in source


def test_the_notification_takes_the_title_first_and_the_message_second() -> None:
    source = (SCRIPTS / NOTIFY_YEAR_CHANGES).read_text(encoding="utf-8")

    assert "display notification (item 2 of argv) with title (item 1 of argv)" in source
    assert 'return "OK"' in source


def test_the_id_read_uses_the_separators_the_parser_splits_on() -> None:
    source = (SCRIPTS / FETCH_TRACK_IDS).read_text(encoding="utf-8")

    assert f"set fieldSeparator to ASCII character {ord(FIELD_SEPARATOR)}" in source
    assert f"set lineSeparator to ASCII character {ord(LINE_SEPARATOR)}" in source


@pytest.mark.skipif(OSACOMPILE is None, reason="osacompile is macOS only")
@pytest.mark.parametrize("name", [ASK_YEAR_DRIFT, NOTIFY_YEAR_CHANGES, FETCH_TRACK_IDS])
def test_scripts_compile(name: str, tmp_path: Path) -> None:
    assert OSACOMPILE is not None
    result = subprocess.run([OSACOMPILE, "-o", str(tmp_path / "out.scpt"), str(SCRIPTS / name)], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
