"""Tests for launchctl/bin/run-daemon.sh: git fetch retries, the offline skip, and the failures that must notify."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

RUN_DAEMON_SCRIPT = Path(__file__).resolve().parents[2] / "launchctl" / "bin" / "run-daemon.sh"
_GIT_PATH = shutil.which("git")
APPLE_SCRIPTS_DIR = "~/.local/share/genreupdater/app/applescripts"

# A default route exists once $HOME/network-up does. macOS route(8) exits 0 whether or not
# the route exists; only its output tells them apart, so the fake keeps that contract.
ROUTE_SCRIPT = """if [[ -e "$HOME/network-up" ]]; then
    printf "destination: default\\n  interface: en0\\n"
else
    echo "route: writing to routing socket: not in table" >&2
fi"""

# A route command that cannot run at all says nothing about the network
BROKEN_ROUTE_SCRIPT = 'echo "route: command not found" >&2\nexit 127'

# The network, and with it origin, comes up while the daemon waits for the second time
NETWORK_UP_ON_SECOND_SLEEP = """calls_file="$HOME/sleep-calls"
calls=$(( $(cat "$calls_file" 2>/dev/null || echo 0) + 1 ))
echo "$calls" > "$calls_file"
if (( calls >= 2 )); then
    touch "$HOME/network-up"
    ln -sfn "{upstream}" "{origin_link}"
fi"""

# Origin becomes reachable during the first wait, while the default route is already there
ORIGIN_UP_ON_FIRST_SLEEP = 'ln -sfn "{upstream}" "{origin_link}"'

# Record each requested wait, so a test can add up how long the daemon waits
RECORDING_SLEEP = 'echo "$1" >> "$HOME/sleep-seconds"'

# timeout(1) runs the command; when it has to kill it, it prints nothing and exits 124
PASSING_TIMEOUT = 'shift\nexec "$@"'
EXPIRED_TIMEOUT = "exit 124"


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
    home: Path
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

    def log_path(self, name: str) -> Path:
        """Get the path of a daemon log file.

        Args:
            name: File name inside the logs directory.

        Returns:
            The path under the daemon's logs directory.
        """
        return self.home / ".local" / "state" / "genreupdater" / "logs" / name


def _make_sandbox(
    tmp_path: Path,
    *,
    network_up: bool = False,
    sleep_body: str = "exit 0",
    route_body: str = ROUTE_SCRIPT,
    timeout_body: str = PASSING_TIMEOUT,
    apple_scripts_dir: str = APPLE_SCRIPTS_DIR,
) -> DaemonSandbox:
    """Build the daemon layout from common.sh with real git and fakes for the external commands.

    Origin is reachable only through ``network/origin``, a link that nothing creates unless
    ``sleep_body`` does, so a fetch fails until then. ``sleep_body`` is a ``str.format`` template
    that receives ``{upstream}`` and ``{origin_link}``, so literal braces must be doubled; the
    other bodies are written as they are.
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
    (config_dir / "my-config.yaml").write_text(f"apple_scripts_dir: {apple_scripts_dir}\n", encoding="utf-8")
    (config_dir / ".env").write_text("DISCOGS_TOKEN=test\n", encoding="utf-8")

    notification_log = tmp_path / "notifications.txt"
    _write_executable(fake_bin / "route", route_body)
    _write_executable(fake_bin / "sleep", sleep_body.format(upstream=upstream, origin_link=origin_link))
    _write_executable(fake_bin / "lockf", "exit 0")
    _write_executable(fake_bin / "timeout", timeout_body)
    _write_executable(fake_bin / "uv", "exit 0")
    # notify.sh passes title, message and sound after "--"; keep the title
    _write_executable(fake_bin / "osascript", f'while (( $# )) && [[ $1 != -- ]]; do shift; done\nprintf "%s\\n" "$2" >> "{notification_log}"')
    return DaemonSandbox(environment=environment, home=home, app_dir=app_dir, notification_log=notification_log)


def test_offline_launch_is_skipped_without_running_the_pipeline(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path)

    result = sandbox.run()

    # EX_TEMPFAIL, the same exit as a run skipped because Music.app is closed
    assert result.returncode == 75, result.stdout + result.stderr
    assert sandbox.notification_titles() == []
    # The pipeline step's >> redirect creates stdout.log, even though the fake uv prints nothing
    assert not sandbox.log_path("stdout.log").exists()
    assert "run skipped" in sandbox.log_path("daemon.log").read_text(encoding="utf-8")


def test_network_coming_up_during_the_wait_pins_the_new_release(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, sleep_body=NETWORK_UP_ON_SECOND_SLEEP)

    result = sandbox.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert sandbox.pinned_tag() == "v1.1.0"
    assert sandbox.notification_titles() == ["Genre Updater"]


def test_fetch_failing_while_the_route_is_up_is_retried(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, network_up=True, sleep_body=ORIGIN_UP_ON_FIRST_SLEEP)

    result = sandbox.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert sandbox.pinned_tag() == "v1.1.0"
    assert sandbox.notification_titles() == ["Genre Updater"]


def test_unreachable_origin_on_a_working_network_fails_loudly(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, network_up=True)

    result = sandbox.run()

    assert result.returncode == 1, result.stdout + result.stderr
    assert sandbox.notification_titles() == ["Genre Updater Error"]
    assert "git fetch failed" in sandbox.log_path("daemon.log").read_text(encoding="utf-8")


def test_route_that_cannot_run_is_not_taken_for_offline(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, route_body=BROKEN_ROUTE_SCRIPT)

    result = sandbox.run()

    assert result.returncode == 1, result.stdout + result.stderr
    assert sandbox.notification_titles() == ["Genre Updater Error"]


@pytest.mark.parametrize("network_up", [False, True], ids=["offline", "unreachable-origin"])
def test_failing_fetch_is_retried_for_a_minute(tmp_path: Path, network_up: bool) -> None:
    sandbox = _make_sandbox(tmp_path, network_up=network_up, sleep_body=RECORDING_SLEEP)

    sandbox.run()

    waits = (sandbox.home / "sleep-seconds").read_text(encoding="utf-8").split()
    assert sum(int(seconds) for seconds in waits) == 60


def test_fetch_killed_by_its_time_cap_is_reported_as_timed_out(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, network_up=True, timeout_body=EXPIRED_TIMEOUT)

    result = sandbox.run()

    assert result.returncode == 1, result.stdout + result.stderr
    assert sandbox.notification_titles() == ["Genre Updater Error"]
    assert "timed out" in sandbox.log_path("daemon.log").read_text(encoding="utf-8")


def test_missing_apple_scripts_dir_fails_loudly(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, network_up=True, apple_scripts_dir="~/.local/share/genreupdater/app/missing")

    result = sandbox.run()

    assert result.returncode == 1, result.stdout + result.stderr
    assert sandbox.notification_titles() == ["Genre Updater Error"]
