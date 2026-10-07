"""Tests for launchctl/bin/run-daemon.sh: release pinning when the network is not up at launch."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

RUN_DAEMON_SCRIPT = Path(__file__).resolve().parents[2] / "launchctl" / "bin" / "run-daemon.sh"
_GIT_PATH = shutil.which("git")

# A default route exists once $HOME/network-up does. macOS route(8) exits 0 whether or not
# the route exists; only its stdout tells them apart, so the fake keeps that contract.
ROUTE_SCRIPT = """if [[ -e "$HOME/network-up" ]]; then
    printf "destination: default\\n  interface: en0\\n"
else
    echo "route: writing to routing socket: not in table" >&2
fi"""

# The network, and with it origin, comes up while the daemon waits for the second time
NETWORK_UP_ON_SECOND_SLEEP = """calls_file="$HOME/sleep-calls"
calls=$(( $(cat "$calls_file" 2>/dev/null || echo 0) + 1 ))
echo "$calls" > "$calls_file"
if (( calls >= 2 )); then
    touch "$HOME/network-up"
    ln -sfn "{upstream}" "{origin_link}"
fi"""


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _git(environment: dict[str, str], *arguments: str) -> str:
    if not _GIT_PATH:
        pytest.skip("git not installed")
    completed = subprocess.run([_GIT_PATH, *arguments], check=True, capture_output=True, text=True, env=environment)
    return completed.stdout.strip()


@dataclass(frozen=True)
class DaemonSandbox:
    """Fake home with an app clone pinned to v1.0.0, while origin already has v1.1.0."""

    environment: dict[str, str]
    app_dir: Path
    notification_log: Path

    def run(self) -> subprocess.CompletedProcess[str]:
        """Run the daemon script.

        Returns:
            The result of the subprocess execution.
        """
        return subprocess.run(["/bin/bash", str(RUN_DAEMON_SCRIPT)], capture_output=True, text=True, env=self.environment, timeout=60, check=False)

    def pinned_tag(self) -> str:
        """Get the tag to which the app is pinned.

        Returns:
            The pinned tag.
        """
        return _git(self.environment, "-C", str(self.app_dir), "describe", "--tags", "--exact-match", "HEAD")

    def notification_titles(self) -> list[str]:
        """Get the titles of the notifications.

        Returns:
            A list of notification titles.
        """
        if not self.notification_log.exists():
            return []
        return self.notification_log.read_text(encoding="utf-8").splitlines()


def _make_sandbox(tmp_path: Path, *, network_up: bool = False, sleep_body: str = "exit 0") -> DaemonSandbox:
    """Build the daemon layout from common.sh with real git and fakes for the external commands.

    Origin is reachable only through ``network/origin``, a link that nothing creates unless
    ``sleep_body`` brings the network up, so a fetch fails until then.
    """
    home = tmp_path / "home"
    fake_bin = home / ".local" / "bin"
    fake_bin.mkdir(parents=True)
    if network_up:
        (home / "network-up").touch()
    environment = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }

    upstream = tmp_path / "upstream"
    _git(environment, "init", "--quiet", "--initial-branch=main", str(upstream))
    (upstream / "applescripts").mkdir()
    (upstream / "applescripts" / ".keep").write_text("", encoding="utf-8")
    _git(environment, "-C", str(upstream), "add", "--all")
    _git(environment, "-C", str(upstream), "commit", "--quiet", "--message=first release")
    _git(environment, "-C", str(upstream), "tag", "v1.0.0")

    app_dir = home / ".local" / "share" / "genreupdater" / "app"
    _git(environment, "clone", "--quiet", str(upstream), str(app_dir))
    _git(environment, "-C", str(app_dir), "checkout", "--quiet", "--detach", "refs/tags/v1.0.0")
    origin_link = tmp_path / "network" / "origin"
    origin_link.parent.mkdir()
    _git(environment, "-C", str(app_dir), "remote", "set-url", "origin", str(origin_link))

    _git(environment, "-C", str(upstream), "commit", "--quiet", "--allow-empty", "--message=second release")
    _git(environment, "-C", str(upstream), "tag", "v1.1.0")

    config_dir = home / ".config" / "genreupdater"
    config_dir.mkdir(parents=True)
    (config_dir / "my-config.yaml").write_text("apple_scripts_dir: ~/.local/share/genreupdater/app/applescripts\n", encoding="utf-8")
    (config_dir / ".env").write_text("DISCOGS_TOKEN=test\n", encoding="utf-8")

    notification_log = tmp_path / "notifications.txt"
    _write_executable(fake_bin / "route", ROUTE_SCRIPT)
    _write_executable(fake_bin / "sleep", sleep_body.format(upstream=upstream, origin_link=origin_link))
    _write_executable(fake_bin / "lockf", "exit 0")
    _write_executable(fake_bin / "timeout", 'shift\nexec "$@"')
    _write_executable(fake_bin / "uv", "exit 0")
    # notify.sh passes title, message and sound after "--"; keep the title
    _write_executable(fake_bin / "osascript", f'while [[ $1 != -- ]]; do shift; done\nprintf "%s\\n" "$2" >> "{notification_log}"')
    return DaemonSandbox(environment=environment, app_dir=app_dir, notification_log=notification_log)


def test_offline_launch_is_skipped_without_running_the_pipeline(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path)

    result = sandbox.run()

    # EX_TEMPFAIL, the same exit as a run skipped because Music.app is closed
    assert result.returncode == 75, result.stdout + result.stderr
    assert sandbox.pinned_tag() == "v1.0.0"
    assert sandbox.notification_titles() == []


def test_network_coming_up_during_the_wait_pins_the_new_release(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, sleep_body=NETWORK_UP_ON_SECOND_SLEEP)

    result = sandbox.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert sandbox.pinned_tag() == "v1.1.0"
    assert sandbox.notification_titles() == ["Genre Updater"]


def test_unreachable_origin_on_a_working_network_fails_loudly(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, network_up=True)

    result = sandbox.run()

    assert result.returncode == 1, result.stdout + result.stderr
    assert sandbox.pinned_tag() == "v1.0.0"
    assert sandbox.notification_titles() == ["Genre Updater Error"]
