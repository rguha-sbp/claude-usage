import json
from pathlib import Path

import pytest

from usage.config import Config, load_config, REPO_ROOT


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.port == 8765
    assert cfg.threshold_tokens == 200000
    assert cfg.warn_tokens == 150000
    assert cfg.alert_tokens == 180000
    assert cfg.ccusage_command == ["npx", "-y", "ccusage@latest"]
    assert cfg.claude_dir == Path.home() / ".claude"
    assert cfg.state_dir == Path.home() / ".claude" / "usage-state"
    assert cfg.refresh_seconds == 60
    assert cfg.db_path == REPO_ROOT / "usage.db"
    assert cfg.dashboard_dir == REPO_ROOT / "dashboard"


def test_file_overrides_and_tilde_expansion(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "port": 9000,
        "threshold_tokens": 150000,
        "statusline": {"warn_tokens": 100000, "alert_tokens": 120000},
        "claude_dir": "~/claude-alt",
        "ccusage_command": ["ccusage"],
    }))
    cfg = load_config(p)
    assert cfg.port == 9000
    assert cfg.threshold_tokens == 150000
    assert cfg.warn_tokens == 100000 and cfg.alert_tokens == 120000
    assert cfg.claude_dir == Path.home() / "claude-alt"
    assert cfg.ccusage_command == ["ccusage"]
    assert cfg.notifier == "/opt/homebrew/bin/terminal-notifier"  # default kept


def test_invalid_thresholds_rejected(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"statusline": {"warn_tokens": 200000, "alert_tokens": 100000}}))
    with pytest.raises(ValueError):
        load_config(p)


def test_non_positive_rejected(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"port": 0}))
    with pytest.raises(ValueError):
        load_config(p)


def test_repo_config_json_is_valid():
    cfg = load_config(REPO_ROOT / "config.json")
    assert isinstance(cfg, Config)


def test_claude_config_dir_env_used_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "alt"))
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.claude_dir == tmp_path / "alt"
    assert cfg.state_dir == tmp_path / "alt" / "usage-state"


def test_claude_config_dir_env_wins_over_explicit_claude_dir(tmp_path, monkeypatch):
    """Fix round 2: CLAUDE_CONFIG_DIR, when set, overrides config.json's
    claude_dir unconditionally, so every component (installer, hook,
    statusline, dashboard) agrees on where Claude Code's config lives even
    when the shipped config.json sets an explicit claude_dir."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "alt"))
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"claude_dir": str(tmp_path / "explicit")}))
    cfg = load_config(p)
    assert cfg.claude_dir == tmp_path / "alt"
    assert cfg.state_dir == tmp_path / "alt" / "usage-state"


def test_explicit_claude_dir_used_when_env_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"claude_dir": str(tmp_path / "explicit")}))
    assert load_config(p).claude_dir == tmp_path / "explicit"


def test_office_thresholds_default_and_override(tmp_path):
    from usage.config import load_config
    assert load_config(tmp_path / "none.json").office_working_seconds == 60
    assert load_config(tmp_path / "none.json").office_gone_after_seconds == 86400
    p = tmp_path / "c.json"
    p.write_text('{"office": {"working_seconds": 30}}')
    cfg = load_config(p)
    assert cfg.office_working_seconds == 30 and cfg.office_gone_after_seconds == 86400
