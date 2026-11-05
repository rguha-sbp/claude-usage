"""Tests for install.sh's path resolution (fix round 1, finding 2).

install.sh must honour CLAUDE_CONFIG_DIR (falling back to $HOME/.claude)
for both the settings file and the usage-state dir, exactly like
usage/config.py and hooks/precompact-notify.sh. These tests run the real
script but always point HOME and CLAUDE_CONFIG_DIR at throwaway tmp_path
directories, and strip PATH so brew/npx are not found (no real installs, no
network calls) -- the real ~/.claude is never touched.
"""
import json
import os
import subprocess

from usage.config import REPO_ROOT

INSTALL_SH = REPO_ROOT / "install.sh"

# No /opt/homebrew/bin (brew, terminal-notifier) and no npx: steps 2 and 3
# warn instead of attempting a real install or a network call. /usr/bin has
# python3 on macOS.
SANDBOXED_PATH = "/usr/bin:/bin"


def run_install(tmp_path, extra_env=None):
    home = tmp_path / "home"
    claude_dir = tmp_path / "claude-config"
    home.mkdir()
    env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(claude_dir), "PATH": SANDBOXED_PATH}
    if extra_env:
        env.update(extra_env)
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


def test_settings_land_under_claude_config_dir_not_home(tmp_path):
    result, home, claude_dir = run_install(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    settings = claude_dir / "settings.json"
    assert settings.exists()
    written = json.loads(settings.read_text())
    assert "autoCompactWindow" not in written
    assert written["cleanupPeriodDays"] == 90

    # Never falls back to $HOME/.claude when CLAUDE_CONFIG_DIR is set.
    assert not (home / ".claude" / "settings.json").exists()


def test_usage_state_dir_created_under_claude_config_dir(tmp_path):
    result, home, claude_dir = run_install(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (claude_dir / "usage-state").is_dir()
    assert not (home / ".claude" / "usage-state").exists()


def test_reports_ok_for_settings_step(tmp_path):
    result, _home, _claude_dir = run_install(tmp_path)
    assert "OK: statusline, PreCompact hook and cleanupPeriodDays merged." in result.stdout


def test_falls_back_to_home_claude_when_claude_config_dir_unset(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": SANDBOXED_PATH}
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
