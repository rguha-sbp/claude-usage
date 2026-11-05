"""Configuration loading for the usage dashboard (spec §8)."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: Dict[str, Any] = {
    "port": 8765,
    "threshold_tokens": 200000,
    "statusline": {"warn_tokens": 150000, "alert_tokens": 180000},
    "ccusage_command": ["npx", "-y", "ccusage@latest"],
    "claude_dir": "~/.claude",
    "notifier": "/opt/homebrew/bin/terminal-notifier",
    "refresh_seconds": 60,
    "db_path": "usage.db",
    "state_dir": "~/.claude/usage-state",
    # Office tab (spec Sec 12): a session seen within working_seconds is
    # working, otherwise asleep; a session with no known process id is
    # dropped once its last sighting is older than gone_after_seconds.
    "office": {"working_seconds": 60, "gone_after_seconds": 86400},
}


@dataclass
class Config:
    port: int
    threshold_tokens: int
    warn_tokens: int
    alert_tokens: int
    ccusage_command: List[str]
    claude_dir: Path
    notifier: str
    refresh_seconds: int
    db_path: Path
    state_dir: Path
    dashboard_dir: Path
    office_working_seconds: int
    office_gone_after_seconds: int


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _positive_int(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("%s must be a positive integer, got %r" % (name, value))
    return value


def _resolve_claude_dir(raw: Dict[str, Any], data: Dict[str, Any]) -> Path:
    """CLAUDE_CONFIG_DIR, when set and non-empty, wins unconditionally over
    config.json's claude_dir (fix round 2): install.sh and the PreCompact
    hook already honour it unconditionally, so every component must agree
    on where Claude Code's config lives, even when config.json sets an
    explicit claude_dir."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env.split(",")[0]).expanduser()
    return Path(data["claude_dir"]).expanduser()


def load_config(path: Optional[Path] = None) -> Config:
    if path is None:
        path = REPO_ROOT / "config.json"
    raw: Dict[str, Any] = {}
    if path.exists():
        raw = json.loads(path.read_text())
    data = _merge(DEFAULTS, raw)
    sl = data["statusline"]
    warn = _positive_int("statusline.warn_tokens", sl["warn_tokens"])
    alert = _positive_int("statusline.alert_tokens", sl["alert_tokens"])
    if warn >= alert:
        raise ValueError("statusline.warn_tokens must be lower than alert_tokens")
    db_path = Path(data["db_path"]).expanduser()
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path
    cmd = data["ccusage_command"]
    if not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd) or not cmd:
        raise ValueError("ccusage_command must be a non-empty list of strings")
    return Config(
        port=_positive_int("port", data["port"]),
        threshold_tokens=_positive_int("threshold_tokens", data["threshold_tokens"]),
        warn_tokens=warn,
        alert_tokens=alert,
        ccusage_command=list(cmd),
        claude_dir=_resolve_claude_dir(raw, data),
        notifier=str(data["notifier"]),
        refresh_seconds=_positive_int("refresh_seconds", data["refresh_seconds"]),
        db_path=db_path,
        state_dir=(Path(data["state_dir"]).expanduser() if "state_dir" in raw
                   else _resolve_claude_dir(raw, data) / "usage-state"),
        dashboard_dir=REPO_ROOT / "dashboard",
        office_working_seconds=_positive_int("office.working_seconds", data["office"]["working_seconds"]),
        office_gone_after_seconds=_positive_int("office.gone_after_seconds", data["office"]["gone_after_seconds"]),
    )
