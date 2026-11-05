"""Tests for the PreCompact hook (hooks/precompact-notify.sh).

The hook must always exit 0 and print nothing to stdout (Claude Code shows
PreCompact hook stdout to the user). It notifies via $USAGE_NOTIFIER only
when trigger == "auto", looking up the session title from the statusline's
state file at $USAGE_STATE_DIR/<sanitised session_id>.json.
"""
import json
import os
import stat
import subprocess
import time
from pathlib import Path

from usage.config import REPO_ROOT

HOOK = REPO_ROOT / "hooks" / "precompact-notify.sh"


def fake_notifier(tmp_path: Path) -> Path:
    """A stub terminal-notifier: appends its argv (as a single line) to a log file."""
    log = tmp_path / "notify.log"
    script = tmp_path / "notifier.sh"
    script.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "{}"\n'.format(log))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def run_hook(tmp_path: Path, payload: str, notifier: Path = None, state_dir: Path = None):
    env = dict(os.environ)
    env["USAGE_NOTIFIER"] = str(notifier if notifier is not None else fake_notifier(tmp_path))
    env["USAGE_STATE_DIR"] = str(state_dir if state_dir is not None else (tmp_path / "state"))
    return subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )


def wait_for_log(log_path: Path, timeout: float = 3.0) -> str:
    """The notifier runs detached in the background; poll briefly for its output."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_path.exists():
            content = log_path.read_text()
            if content:
                return content
        time.sleep(0.05)
    return log_path.read_text() if log_path.exists() else ""


def test_hook_is_executable():
    assert HOOK.exists() and os.access(HOOK, os.X_OK)


def test_auto_trigger_with_state_notifies(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "abc.json").write_text(json.dumps({"announced": "warn", "title": "My session"}))
    r = run_hook(
        tmp_path,
        json.dumps({"session_id": "abc", "trigger": "auto", "hook_event_name": "PreCompact"}),
        state_dir=state_dir,
    )
    assert r.returncode == 0
    assert r.stdout == ""
    log = wait_for_log(tmp_path / "notify.log")
    assert '-message Auto-compact starting: My session' in log or '-message "Auto-compact starting: My session"' in log
    assert "-group abc" in log


def test_manual_trigger_does_not_notify(tmp_path):
    r = run_hook(tmp_path, json.dumps({"session_id": "abc", "trigger": "manual"}))
    assert r.returncode == 0
    assert r.stdout == ""
    time.sleep(0.3)
    assert not (tmp_path / "notify.log").exists()


def test_missing_state_file_falls_back_to_this_session(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    r = run_hook(
        tmp_path,
        json.dumps({"session_id": "no-such-session", "trigger": "auto"}),
        state_dir=state_dir,
    )
    assert r.returncode == 0
    assert r.stdout == ""
    log = wait_for_log(tmp_path / "notify.log")
    assert "Auto-compact starting: this session" in log


def test_empty_title_falls_back_to_this_session(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "abc.json").write_text(json.dumps({"announced": "none", "title": ""}))
    r = run_hook(
        tmp_path,
        json.dumps({"session_id": "abc", "trigger": "auto"}),
        state_dir=state_dir,
    )
    assert r.returncode == 0
    assert r.stdout == ""
    log = wait_for_log(tmp_path / "notify.log")
    assert "Auto-compact starting: this session" in log


def test_malformed_json_exits_cleanly(tmp_path):
    r = run_hook(tmp_path, "not json")
    assert r.returncode == 0
    assert r.stdout == ""
    time.sleep(0.3)
    assert not (tmp_path / "notify.log").exists()


def test_missing_session_id_exits_cleanly(tmp_path):
    r = run_hook(tmp_path, json.dumps({"trigger": "auto"}))
    assert r.returncode == 0
    assert r.stdout == ""
    time.sleep(0.3)
    assert not (tmp_path / "notify.log").exists()


def test_notifier_missing_path_exits_cleanly(tmp_path):
    missing = tmp_path / "no-such-notifier"
    r = run_hook(
        tmp_path,
        json.dumps({"session_id": "abc", "trigger": "auto"}),
        notifier=missing,
    )
    assert r.returncode == 0
    assert r.stdout == ""


def test_default_state_dir_honours_claude_config_dir(tmp_path):
    """Without USAGE_STATE_DIR set, the hook's default state dir must follow
    CLAUDE_CONFIG_DIR (fix round 1), matching usage/config.py and install.sh,
    not unconditionally $HOME/.claude."""
    claude_dir = tmp_path / "claude-config"
    state_dir = claude_dir / "usage-state"
    state_dir.mkdir(parents=True)
    (state_dir / "abc.json").write_text(json.dumps({"announced": "warn", "title": "Config dir session"}))

    env = dict(os.environ)
    env.pop("USAGE_STATE_DIR", None)
    env["USAGE_NOTIFIER"] = str(fake_notifier(tmp_path))
    env["CLAUDE_CONFIG_DIR"] = str(claude_dir)
    # A decoy $HOME/.claude/usage-state that must NOT be consulted.
    decoy_home = tmp_path / "decoy-home"
    (decoy_home / ".claude" / "usage-state").mkdir(parents=True)
    (decoy_home / ".claude" / "usage-state" / "abc.json").write_text(json.dumps({"title": "Decoy session"}))
    env["HOME"] = str(decoy_home)

    r = subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=json.dumps({"session_id": "abc", "trigger": "auto"}),
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert r.returncode == 0
    assert r.stdout == ""
    log = wait_for_log(tmp_path / "notify.log")
    assert "Auto-compact starting: Config dir session" in log


def test_session_id_path_traversal_is_confined_to_state_dir(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # A file matching the sanitised name, inside the state dir, should be found.
    (state_dir / ".._.._x.json").write_text(json.dumps({"title": "Traversal target"}))
    # A file at the literal (unsanitised) traversal target, outside the state dir, must not be touched.
    outside = tmp_path / "x.json"
    outside.write_text(json.dumps({"title": "Outside file"}))

    r = run_hook(
        tmp_path,
        json.dumps({"session_id": "../../x", "trigger": "auto"}),
        state_dir=state_dir,
    )
    assert r.returncode == 0
    assert r.stdout == ""
    log = wait_for_log(tmp_path / "notify.log")
    assert "Auto-compact starting: Traversal target" in log
    assert "Outside file" not in log
