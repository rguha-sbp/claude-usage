"""Tests for install.sh's path resolution (fix round 1, finding 2).

install.sh must honour CLAUDE_CONFIG_DIR (falling back to $HOME/.claude)
for both the settings file and the usage-state dir, exactly like
usage/config.py and hooks/precompact-notify.sh. These tests run the real
script but always point HOME and CLAUDE_CONFIG_DIR at throwaway tmp_path
directories, and strip PATH so brew/npx are not found (no real installs, no
network calls) -- the real ~/.claude is never touched. A fake `uv` on PATH
answers `uv python find` with the interpreter running these tests, so no
real Python is installed either.
"""
import json
import os
import shlex
import subprocess
import sys

import pytest

from usage.config import REPO_ROOT

INSTALL_SH = REPO_ROOT / "install.sh"

# No /opt/homebrew/bin (brew, terminal-notifier) and no npx: those steps
# warn instead of attempting a real install or a network call.
SANDBOXED_PATH = "/usr/bin:/bin"

# `python find` succeeds once `python install` has run (or at once, when the
# test pre-creates the marker); every call is logged so tests can assert on it.
FAKE_UV = """#!/bin/bash
echo "$*" >> "$FAKE_UV_DIR/calls"
case "$*" in
    "python find --system 3.15") [ -e "$FAKE_UV_DIR/installed" ] && echo "%s" ;;
    "python install 3.15") touch "$FAKE_UV_DIR/installed" ;;
    *) exit 2 ;;
esac
""" % sys.executable


@pytest.fixture
def fake_uv(tmp_path):
    bin_dir = tmp_path / "fake-uv"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    (bin_dir / "installed").touch()
    return bin_dir


def run_install(tmp_path, uv_dir):
    home = tmp_path / "home"
    claude_dir = tmp_path / "claude-config"
    home.mkdir()
    path = SANDBOXED_PATH if uv_dir is None else "%s:%s" % (uv_dir, SANDBOXED_PATH)
    env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(claude_dir), "PATH": path, "FAKE_UV_DIR": str(uv_dir)}
    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, home, claude_dir


def test_is_executable():
    assert INSTALL_SH.exists() and os.access(INSTALL_SH, os.X_OK)


def test_settings_land_under_claude_config_dir_not_home(tmp_path, fake_uv):
    result, home, claude_dir = run_install(tmp_path, fake_uv)

    assert result.returncode == 0, result.stdout + result.stderr
    settings = claude_dir / "settings.json"
    assert settings.exists()
    written = json.loads(settings.read_text())
    assert "autoCompactWindow" not in written
    assert written["cleanupPeriodDays"] == 90

    # Never falls back to $HOME/.claude when CLAUDE_CONFIG_DIR is set.
    assert not (home / ".claude" / "settings.json").exists()


def test_usage_state_dir_created_under_claude_config_dir(tmp_path, fake_uv):
    result, home, claude_dir = run_install(tmp_path, fake_uv)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (claude_dir / "usage-state").is_dir()
    assert not (home / ".claude" / "usage-state").exists()


def test_reports_ok_for_settings_step(tmp_path, fake_uv):
    result, _home, _claude_dir = run_install(tmp_path, fake_uv)
    assert "OK: statusline, PreCompact hook and cleanupPeriodDays merged." in result.stdout


def test_falls_back_to_home_claude_when_claude_config_dir_unset(tmp_path, fake_uv):
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": "%s:%s" % (fake_uv, SANDBOXED_PATH), "FAKE_UV_DIR": str(fake_uv)}
    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".claude" / "settings.json").exists()
    assert (home / ".claude" / "usage-state").is_dir()


def test_statusline_runs_on_the_interpreter_uv_found(tmp_path, fake_uv):
    result, _home, claude_dir = run_install(tmp_path, fake_uv)

    assert result.returncode == 0, result.stdout + result.stderr
    command = json.loads((claude_dir / "settings.json").read_text())["statusLine"]["command"]
    assert command == "%s %s" % (shlex.quote(sys.executable), shlex.quote(str(REPO_ROOT / "statusline/statusline.py")))


def test_installs_python_3_15_with_uv_when_none_is_found(tmp_path, fake_uv):
    (fake_uv / "installed").unlink()

    result, _home, claude_dir = run_install(tmp_path, fake_uv)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = (fake_uv / "calls").read_text().splitlines()
    assert calls[:3] == ["python find --system 3.15", "python install 3.15", "python find --system 3.15"]
    assert (claude_dir / "settings.json").exists()


def test_fails_clearly_without_uv_and_writes_nothing(tmp_path):
    result, _home, claude_dir = run_install(tmp_path, None)

    assert result.returncode == 1
    assert "FAIL: uv not found" in result.stdout
    assert not (claude_dir / "settings.json").exists()
