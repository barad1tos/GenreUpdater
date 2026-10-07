"""Tests for launchctl/bin/run-daemon.sh: release pinning when origin is unreachable at launch."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

RUN_DAEMON_SCRIPT = Path(__file__).resolve().parents[2] / "launchctl" / "bin" / "run-daemon.sh"
_GIT_PATH = shutil.which("git")

# macOS route(8) exits 0 whether or not the route exists; only its stdout tells them apart
ROUTE_MISSING = 'echo "route: writing to routing socket: not in table" >&2'
ROUTE_PRESENT = 'printf "destination: default\\n  interface: en0\\n"'

# The machine never gets a default route during the run
NETWORK_NEVER_UP = "{route_missing}"


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


def _make_sandbox(tmp_path: Path, route_body: str) -> DaemonSandbox:
    """Build the daemon layout from common.sh with real git and fakes for the external commands.

    Origin is reachable only through ``network/origin``, a link that ``route_body`` creates once
    the network comes up, so an unreachable origin and an absent default route coincide.
    """
    home = tmp_path / "home"
    fake_bin = home / ".local" / "bin"
    fake_bin.mkdir(parents=True)
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
    route_script = route_body.format(upstream=upstream, origin_link=origin_link, route_missing=ROUTE_MISSING, route_present=ROUTE_PRESENT)
    _write_executable(fake_bin / "route", route_script)
    _write_executable(fake_bin / "sleep", "exit 0")
    _write_executable(fake_bin / "lockf", "exit 0")
    _write_executable(fake_bin / "timeout", 'shift\nexec "$@"')
    _write_executable(fake_bin / "uv", "exit 0")
    # notify.sh passes title, message and sound after "--"; keep the title
    _write_executable(fake_bin / "osascript", f'while [[ $1 != -- ]]; do shift; done\nprintf "%s\\n" "$2" >> "{notification_log}"')
    return DaemonSandbox(environment=environment, app_dir=app_dir, notification_log=notification_log)


def test_offline_launch_completes_on_the_release_it_already_has(tmp_path: Path) -> None:
    sandbox = _make_sandbox(tmp_path, NETWORK_NEVER_UP)

    result = sandbox.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert sandbox.pinned_tag() == "v1.0.0"
    assert sandbox.notification_titles() == ["Genre Updater"]


def test_launch_before_network_is_up_pins_new_release_once_online(tmp_path: Path) -> None:
    network_up_on_third_check = """
calls_file="$HOME/route-calls"
calls=$(( $(cat "$calls_file" 2>/dev/null || echo 0) + 1 ))
echo "$calls" > "$calls_file"
if (( calls < 3 )); then
    {route_missing}
    exit 0
fi
ln -sfn "{upstream}" "{origin_link}"
{route_present}
"""
    sandbox = _make_sandbox(tmp_path, network_up_on_third_check)

    result = sandbox.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert sandbox.pinned_tag() == "v1.1.0"
    assert sandbox.notification_titles() == ["Genre Updater"]
