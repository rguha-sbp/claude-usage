"""Live sessions for the Office tab (spec Sec 12.2, 13.3).

The statusline stamps every session's state file with `seen`, `cwd`, `model`,
`transcript_path` and the Claude process id. This module turns those files
into rooms (one per project) of workers that are working (or blocked on
a foreground sub-agent that is) or asleep, each with
what it is doing and its sub-agents; a session whose process is gone, or that
has not been seen for a day, is left out. No database is involved.
"""
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .agents import AgentReader, doing_of
from .parser import _as_dict, _safe_parse_ts, _text_of, derive_project

TAIL_BYTES = 64 * 1024
MESSAGE_CHARS = 400


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True   # exists, just not ours to signal
    return True


def tail_activity(path: Path, claude_dir: Path) -> dict[str, Any]:
    """From a transcript's final TAIL_BYTES: the text of the last assistant
    record that has any (truncated to MESSAGE_CHARS) and what the session is
    doing now (`doing_of` of the last assistant record that says). Both None
    when the file is missing, unreadable or outside `claude_dir`."""
    out: dict[str, Any] = {"last_message": None, "doing": None}
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(claude_dir.resolve()):
            return out
        size = resolved.stat().st_size
        with open(resolved, "rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return out
    for line in reversed(tail.splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            continue   # the first line of the tail is usually a cut record
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        content = _as_dict(rec.get("message")).get("content")
        if out["doing"] is None:
            out["doing"] = doing_of(content)
        if out["last_message"] is None:
            out["last_message"] = _text_of(content).strip()[:MESSAGE_CHARS] or None
        if out["doing"] is not None and out["last_message"] is not None:
            break
    return out


class Office:
    """Reads the state directory on every `payload()` call; keeps only the
    start of each session's current working streak, and the sub-agent
    reader's running totals, between calls."""

    def __init__(self, state_dir: Path, claude_dir: Path, working_seconds: int = 60,
                 gone_after_seconds: int = 86400, alive: Callable[[int], bool] = process_alive) -> None:
        self.state_dir = state_dir
        self.claude_dir = claude_dir
        self.working_seconds = working_seconds
        self.gone_after_seconds = gone_after_seconds
        self.alive = alive
        self.reader = AgentReader(claude_dir, working_seconds)
        self._streak_start: dict[str, str] = {}   # session_id -> ISO `seen` that began the streak

    def _states(self) -> list[dict[str, Any]]:
        out = []
        try:
            files = sorted(self.state_dir.glob("*.json"))
        except OSError:
            return out
        for path in files:
            try:
                state = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(state, dict) and state.get("session_id") and state.get("seen") and state.get("cwd"):
                out.append(state)
        return out

    def _worker(self, state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        seen = _safe_parse_ts(str(state.get("seen")))
        if seen is None:
            return None
        sid = state["session_id"]
        pid = state.get("pid")
        if isinstance(pid, int):
            if not self.alive(pid):
                self._streak_start.pop(sid, None)
                return None
        elif now - seen > timedelta(seconds=self.gone_after_seconds):
            self._streak_start.pop(sid, None)
            return None
        act: dict[str, Any] = {"last_message": None, "doing": None}
        agents, hidden = [], 0
        if state.get("transcript_path"):
            transcript = Path(str(state["transcript_path"]))
            act = tail_activity(transcript, self.claude_dir)
            agents, hidden = self.reader.session_agents(transcript, now)
        # a lead blocked on a foreground sub-agent is still at work; a
        # background agent's work is not its lead's
        working = (now - seen <= timedelta(seconds=self.working_seconds)
                   or any(a.state == "running" and not a.background for a in agents))
        seen_iso = seen.isoformat(timespec="seconds")
        if working:
            since = self._streak_start.setdefault(sid, seen_iso)
        else:
            self._streak_start.pop(sid, None)
            since = seen_iso
        return {"session_id": sid, "title": state.get("title") or sid, "model": state.get("model") or "",
                "state": "working" if working else "asleep", "since": since,
                "last_message": None if working else act["last_message"],
                "doing": act["doing"] if working else None,
                "agents": [asdict(a) for a in agents], "agents_hidden": hidden}

    def payload(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        rooms: dict[str, dict[str, Any]] = {}
        read: set[Path] = set()
        for state in self._states():
            worker = self._worker(state, now)
            if worker is None:
                continue
            if state.get("transcript_path"):
                folder = self.reader.session_dir(Path(str(state["transcript_path"])))
                if folder is not None:
                    read.add(folder)
            cwd = str(state["cwd"])
            project = derive_project(cwd)
            root = cwd.split("/.claude/worktrees/", 1)[0]
            room = rooms.setdefault(project, {"project": project, "cwd": root, "workers": []})
            room["workers"].append(worker)
        self.reader.retain(read)
        for room in rooms.values():
            room["workers"].sort(key=lambda w: w["title"].lower())
        ordered = sorted(rooms.values(), key=lambda r: (-len(r["workers"]), r["project"].lower()))
        return {"generated_at": now.isoformat(timespec="seconds"), "working_seconds": self.working_seconds,
                "rooms": ordered}
