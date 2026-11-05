#!/usr/bin/env python3.15
"""Claude Code statusline: one coloured line plus threshold notifications (spec §4.7).

Invoked by Claude Code with the statusline JSON on stdin after every assistant
message. Binding rules: always exit 0, never raise, finish in milliseconds
(no network, no transcript parsing), and swallow every notification failure.
"""
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from usage.config import load_config  # noqa: E402

type Notifier = Callable[[str, str, str], None]
ANSI = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m", "reset": "\033[0m"}

# Fallback defaults used when config loading itself fails (spec §8 defaults).
FALLBACK_WARN_TOKENS = 150000
FALLBACK_ALERT_TOKENS = 180000
FALLBACK_NOTIFIER = "/opt/homebrew/bin/terminal-notifier"


def _fallback_state_dir() -> Path:
    return Path("~/.claude/usage-state").expanduser()


_UNSAFE_SESSION_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")
MAX_SESSION_ID_LEN = 64


def _safe_state_path(state_dir: Path, session_id: str) -> Path | None:
    """Sanitise session_id to a filename confined to state_dir (spec §4.7 hardening).

    Any character outside [A-Za-z0-9._-] (notably '/') is replaced with '_', which by
    itself prevents any directory traversal since PathLib only treats '/' as a
    separator. The resolved-parent check is defense in depth on top of that; on a
    mismatch (or an all-invalid id that sanitises to empty), the caller must treat
    this as "no session": no state read/write, no notification.
    """
    sanitized = _UNSAFE_SESSION_ID_CHARS.sub("_", str(session_id))[:MAX_SESSION_ID_LEN]
    if not sanitized:
        return None
    path = state_dir / ("%s.json" % sanitized)
    try:
        if path.resolve().parent != state_dir.resolve():
            return None
    except OSError:
        return None
    return path


def _to_number(value):
    """Best-effort numeric coercion. bool/None/anything non-numeric -> None (missing)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def context_tokens(data: dict) -> int | None:
    """Current context tokens: current_usage sum, else used_percentage * window size, else None.

    Each numeric field is coerced tolerantly (int/float/numeric-string accepted); if any
    of the three current_usage fields is missing or non-numeric, fall back to
    used_percentage * context_window_size (same coercion); if that is unusable too, None.
    """
    cw = data.get("context_window") or {}
    cu = cw.get("current_usage")
    if isinstance(cu, dict):
        input_t = _to_number(cu.get("input_tokens"))
        cache_c = _to_number(cu.get("cache_creation_input_tokens"))
        cache_r = _to_number(cu.get("cache_read_input_tokens"))
        if input_t is not None and cache_c is not None and cache_r is not None:
            return int(input_t + cache_c + cache_r)
    size = _to_number(cw.get("context_window_size"))
    pct = _to_number(cw.get("used_percentage"))
    if size and pct is not None:
        return int(round(size * pct / 100.0))
    return None


def colour_for(ctx: int | None, warn: int, alert: int) -> str:
    if ctx is None or ctx < warn:
        return "green"
    return "red" if ctx >= alert else "yellow"


def _k(n: int) -> str:
    return "%dK" % round(n / 1000.0)


def _window(n: int) -> str:
    return "%dM" % round(n / 1e6) if n >= 1e6 else _k(n)


def render(data: dict, warn: int, alert: int) -> str:
    parts = []
    model = (data.get("model") or {}).get("display_name") or (data.get("model") or {}).get("id")
    if model:
        parts.append(str(model))
    if data.get("session_name"):
        parts.append(str(data["session_name"]))
    ctx = context_tokens(data)
    cw = data.get("context_window") or {}
    if ctx is not None:
        seg = "ctx " + _k(ctx)
        window_size = _to_number(cw.get("context_window_size"))
        if window_size:
            seg += "/" + _window(int(window_size))
            seg += " (%d%%)" % round(100.0 * ctx / window_size)
        colour = colour_for(ctx, warn, alert)
        parts.append(ANSI[colour] + seg + ANSI["reset"])
    cost = _to_number((data.get("cost") or {}).get("total_cost_usd"))
    if cost is not None:
        parts.append("$%.2f" % cost)
    hit = _to_number((data.get("prompt_cache") or {}).get("hit_ratio"))
    if hit is not None:
        parts.append("cache %d%%" % round(100 * hit))
    level = (data.get("effort") or {}).get("level")
    if level:
        parts.append("effort %s" % level)
    return " · ".join(parts)


def read_state(state_dir: Path, session_id: str) -> dict:
    p = _safe_state_path(state_dir, session_id)
    if p is None:
        return {"announced": "none", "title": ""}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"announced": "none", "title": ""}


def write_state(state_dir: Path, session_id: str, state: dict) -> None:
    """Write state atomically: write to a pid-scoped temp file, then os.replace onto the
    final path so a reader never observes a partially-written file.
    """
    p = _safe_state_path(state_dir, session_id)
    if p is None:
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + (".tmp-%d" % os.getpid()))
    tmp.write_text(json.dumps(state))
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------- presence (spec Sec 12.1)
type ProcessTable = dict[int, tuple[int, str]]   # pid -> (ppid, command name)
MAX_ANCESTORS = 8


def _process_table() -> ProcessTable:
    """pid -> (ppid, comm) for every process, from one `ps` call. Empty on any
    failure or when USAGE_NO_PS is set (tests, or a machine where ps is slow)."""
    if os.environ.get("USAGE_NO_PS"):
        return {}
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True, timeout=1).stdout
    except Exception:  # noqa: BLE001 - presence is best-effort
        return {}
    table: ProcessTable = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), os.path.basename(parts[2]))
    return table


def find_claude_pid(start: int, table: ProcessTable | None = None) -> int | None:
    """The nearest ancestor of `start` (inclusive) whose command is `claude`:
    the interactive session that spawned this statusline. None when not found."""
    if table is None:
        table = _process_table()
    pid = start
    for _ in range(MAX_ANCESTORS):
        entry = table.get(pid)
        if entry is None:
            return None
        if entry[1] == "claude":
            return pid
        pid = entry[0]
    return None


def stamp_presence(data: dict, state_dir: Path, pid_finder: Callable[[], int | None] | None = None,
                   now: str | None = None) -> None:
    """Record that this session's statusline just ran: `seen`, plus the
    session's cwd, model, transcript path and Claude process id when the
    input carries them. The Office tab (spec Sec 12) reads these; a field
    missing from this run never erases a value recorded earlier."""
    sid = data.get("session_id")
    if not sid or _safe_state_path(state_dir, sid) is None:
        return
    state = read_state(state_dir, sid)
    state["session_id"] = sid
    state["seen"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    model = (data.get("model") or {}).get("display_name") or (data.get("model") or {}).get("id")
    for key, value in (("cwd", data.get("cwd")), ("model", model), ("transcript_path", data.get("transcript_path"))):
        if value:
            state[key] = str(value)
    pid = (pid_finder or (lambda: find_claude_pid(os.getppid())))()
    if pid is not None:
        state["pid"] = pid
    write_state(state_dir, sid, state)


def check_thresholds(data: dict, warn: int, alert: int, state_dir: Path, notify: Notifier) -> str | None:
    """Fire a warn/alert notification at most once per threshold crossing; reset once ctx drops
    back below warn (a compaction happened). Returns the threshold name fired this run, else None.
    """
    sid = data.get("session_id")
    ctx = context_tokens(data)
    if not sid or ctx is None:
        return None
    if _safe_state_path(state_dir, sid) is None:
        # Session id could not be confined to a safe path within state_dir: treat as no session.
        return None
    state = read_state(state_dir, sid)
    new_title = data.get("session_name") or ""
    if new_title:
        state["title"] = new_title  # keep the last known non-empty title otherwise
    title = state.get("title", "")
    display_title = title or "this session"
    announced = state.get("announced", "none")
    fired = None
    if ctx >= alert and announced != "alert":
        notify("Claude Code", "Context %s in %s: compact now" % (_k(alert), display_title), sid)
        state["announced"], fired = "alert", "alert"
    elif ctx >= warn and announced == "none":
        notify("Claude Code", "Context %s in %s: wrap up and /compact" % (_k(warn), display_title), sid)
        state["announced"], fired = "warn", "warn"
    elif ctx < warn and announced != "none":
        state["announced"] = "none"
    write_state(state_dir, sid, state)
    return fired


def make_notifier(path: str) -> Notifier:
    def notify(title: str, message: str, group: str) -> None:
        try:
            if os.path.exists(path):
                subprocess.Popen([path, "-title", title, "-message", message, "-group", group],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception:  # noqa: BLE001 - notification failures must never break the statusline
            pass
    return notify


def prune_state(state_dir: Path, max_age_days: int = 7) -> None:
    """Delete state files older than max_age_days. Best-effort; never raises."""
    try:
        cutoff = time.time() - max_age_days * 86400
        for p in state_dir.glob("*.json"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def main(stdin=None, stdout=None, config_path: Path | None = None, notify: Notifier | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    try:
        raw = stdin.read()
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise ValueError("statusline input is not a JSON object")
    except Exception:  # noqa: BLE001 - garbage/unreadable stdin
        stdout.write("claude\n")
        return 0

    try:
        warn, alert, notifier_path, state_dir = (
            FALLBACK_WARN_TOKENS, FALLBACK_ALERT_TOKENS, FALLBACK_NOTIFIER, _fallback_state_dir())
        try:
            cfg = load_config(config_path)
            warn, alert, notifier_path, state_dir = cfg.warn_tokens, cfg.alert_tokens, cfg.notifier, cfg.state_dir
        except Exception:  # noqa: BLE001 - config load failed: keep hard-coded defaults, still render
            pass

        line = render(data, warn, alert)

        try:
            stamp_presence(data, state_dir)
            check_thresholds(data, warn, alert, state_dir, notify or make_notifier(notifier_path))
            prune_state(state_dir)
        except Exception:  # noqa: BLE001 - notification/state failures must never break the statusline
            pass

        stdout.write((line or "claude") + "\n")
    except Exception:  # noqa: BLE001 - last-resort fallback
        model = (data.get("model") or {}).get("display_name") or (data.get("model") or {}).get("id")
        stdout.write((str(model) if model else "Claude Code") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
