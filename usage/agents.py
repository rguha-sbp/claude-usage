"""Sub-agents of a live session (spec Sec 13.2).

A session's sub-agents sit beside its transcript: `<transcript without
.jsonl>/subagents/agent-<id>.jsonl`, each with an `agent-<id>.meta.json`
naming its type, task, depth and the parent's Agent tool_use id.
`AgentReader` reads each agent transcript incrementally (only the bytes
appended since the last call, the parser's append-only assumption) and
keeps running totals, so the Office's 5-second poll stays cheap however
long an agent runs. No database and no knowledge of the office here.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .parser import _USAGE_FIELDS, _as_dict, _as_list, _as_str, _safe_parse_ts, _usage_from

TOOL_PATIENCE = timedelta(minutes=15)   # a tool call still waiting on its result counts as work this long
QUIET_REPLY = timedelta(seconds=10)      # a legacy text-only reply counts as done after this
DONE_WINDOW = timedelta(hours=2)
DONE_CAP = 20
TARGET_CHARS = 80
TARGET_KEYS = ("file_path", "command", "pattern", "url", "query", "description")
SPAWN_TOOLS = ("Agent", "Task")
HANDBACK = "SubagentHandback"            # how current sub-agents finish


def doing_of(content: Any) -> dict[str, str] | None:
    """What one assistant record says its author is doing: the last tool_use
    as {tool, target}, "replying" for text, None when it holds neither (a
    thinking-only record says nothing new)."""
    if isinstance(content, str):
        return {"tool": "replying", "target": ""} if content.strip() else None
    blocks = [b for b in _as_list(content) if isinstance(b, dict)]
    uses = [b for b in blocks if b.get("type") == "tool_use"]
    if uses:
        inp = _as_dict(uses[-1].get("input"))
        target = next((str(inp[k]) for k in TARGET_KEYS if inp.get(k)), "")
        return {"tool": _as_str(uses[-1].get("name")) or "?", "target": target[:TARGET_CHARS]}
    if any(b.get("type") == "text" and _as_str(b.get("text")) for b in blocks):
        return {"tool": "replying", "target": ""}
    return None


@dataclass
class SubAgent:
    agent_id: str
    type: str
    description: str
    background: bool
    depth: int
    parent: str | None          # agent_id of the spawning agent; None = the main session
    model: str
    tokens: int
    tools: int
    tool_mix: dict[str, int]
    doing: dict[str, str] | None
    started: str
    last: str
    state: str                     # "running" | "asleep" | "done"


@dataclass
class _Tally:
    """Running totals for one agent transcript."""
    size: int = 0                                  # bytes consumed; stops before a cut last line
    model: str = ""
    usage: dict[str, dict[str, int]] = field(default_factory=dict)   # message id -> per-field max
    tool_mix: dict[str, int] = field(default_factory=dict)
    spawned: list[str] = field(default_factory=list)                 # Agent/Task tool_use ids
    doing: dict[str, str] | None = None
    handed_back: bool = False
    replied_last: bool = False
    awaiting: bool = False                         # the last tool call has no result yet
    started: str | None = None
    last: str | None = None

    @property
    def tokens(self) -> int:
        return sum(u["input_tokens"] + u["cache_write"] + u["cache_read"] + u["output"] for u in self.usage.values())


def _fold(t: _Tally, rec: dict[str, Any]) -> None:
    ts = _as_str(rec.get("timestamp"))
    if ts:
        t.started = t.started or ts
        t.last = ts
    message = _as_dict(rec.get("message"))
    if rec.get("type") == "user":
        t.awaiting = False
    if rec.get("type") != "assistant" or not message:
        return
    t.model = _as_str(message.get("model")) or t.model
    mid = _as_str(message.get("id")) or _as_str(rec.get("uuid"))
    usage = _usage_from(message)
    if mid and usage is not None:
        seen = t.usage.setdefault(mid, dict.fromkeys(_USAGE_FIELDS, 0))
        for name in _USAGE_FIELDS:
            seen[name] = max(seen[name], usage[name])
    content = message.get("content")
    for b in _as_list(content):
        if not (isinstance(b, dict) and b.get("type") == "tool_use"):
            continue
        name = _as_str(b.get("name")) or "?"
        if name == HANDBACK:
            t.handed_back = True
            continue
        t.tool_mix[name] = t.tool_mix.get(name, 0) + 1
        if name in SPAWN_TOOLS and _as_str(b.get("id")):
            t.spawned.append(b["id"])
    doing = doing_of(content)
    if doing is not None:
        t.doing = doing
        t.replied_last = doing["tool"] == "replying"
        t.awaiting = not t.replied_last


def _advance(t: _Tally, path: Path) -> _Tally:
    """Fold in whatever was appended since `t.size`; a file that shrank was
    rewritten and is read again from the start."""
    size = path.stat().st_size
    if size < t.size:
        t = _Tally()
    if size == t.size:
        return t
    with open(path, "rb") as fh:
        fh.seek(t.size)
        chunk = fh.read(size - t.size)
    end = chunk.rfind(b"\n") + 1               # a cut last line waits for the next read
    for line in chunk[:end].splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            _fold(t, rec)
    t.size += end
    return t


def _read_meta(path: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _age(ts: str | None, now: datetime) -> timedelta:
    when = _safe_parse_ts(ts) if ts else None
    return now - when if when is not None else timedelta.max


def _state(t: _Tally, now: datetime, working: timedelta) -> str:
    """Done once handed back; running while it wrote something within
    `working` (the leads' own window) or still waits on a tool's result;
    asleep otherwise, like a quiet lead."""
    quiet = _age(t.last, now)
    if t.handed_back or (t.replied_last and quiet >= QUIET_REPLY):
        return "done"
    return "running" if quiet <= working or (t.awaiting and quiet <= TOOL_PATIENCE) else "asleep"


def _agent(agent_id: str, meta: dict[str, Any], t: _Tally, owner: dict[str, str], now: datetime,
           working: timedelta) -> SubAgent:
    depth = meta.get("spawnDepth")
    return SubAgent(agent_id=agent_id, type=_as_str(meta.get("agentType")) or "agent",
                    description=_as_str(meta.get("description")) or "",
                    background=meta.get("requestShape") == "background",
                    depth=depth if isinstance(depth, int) and depth > 0 else 1,
                    parent=owner.get(_as_str(meta.get("toolUseId")) or ""),
                    model=t.model, tokens=t.tokens, tools=sum(t.tool_mix.values()), tool_mix=dict(t.tool_mix),
                    doing=t.doing, started=t.started or "", last=t.last or "", state=_state(t, now, working))


class AgentReader:
    """One per Office; remembers a `_Tally` per agent transcript between calls."""

    def __init__(self, claude_dir: Path, working_seconds: int = 60) -> None:
        self.claude_dir = claude_dir
        self.working = timedelta(seconds=working_seconds)
        self._tallies: dict[Path, _Tally] = {}

    def session_dir(self, transcript: Path) -> Path | None:
        """The folder holding a session's per-session files, beside its
        transcript; None when the transcript lies outside `claude_dir`."""
        try:
            resolved = transcript.resolve()
            if not resolved.is_relative_to(self.claude_dir.resolve()):
                return None
        except OSError:
            return None
        return resolved.with_suffix("")

    def retain(self, session_dirs: set[Path]) -> None:
        """Forget the tallies of every session whose folder is not in `session_dirs`."""
        self._tallies = {p: t for p, t in self._tallies.items() if p.parent.parent in session_dirs}

    def session_agents(self, transcript_path: Path, now: datetime) -> tuple[list[SubAgent], int]:
        """Running and asleep agents plus the done ones from the last
        DONE_WINDOW (the DONE_CAP most recent), ordered by start; and how
        many done ones were left out."""
        session_dir = self.session_dir(transcript_path)
        folder = session_dir / "subagents" if session_dir is not None else None
        if folder is None or not folder.is_dir():
            return [], 0
        found = []
        for meta_path in sorted(folder.glob("agent-*.meta.json")):
            agent_id = meta_path.name[len("agent-"):-len(".meta.json")]
            jsonl = folder / ("agent-%s.jsonl" % agent_id)
            meta = _read_meta(meta_path)
            if meta is None or not jsonl.is_file():
                continue
            try:
                tally = _advance(self._tallies.get(jsonl, _Tally()), jsonl)
            except OSError:
                continue
            self._tallies[jsonl] = tally
            if tally.started is not None:
                found.append((agent_id, meta, tally))
        owner = {tid: aid for aid, _, t in found for tid in t.spawned}
        agents = sorted((_agent(aid, meta, t, owner, now, self.working) for aid, meta, t in found), key=lambda a: a.started)
        done = [a for a in agents if a.state == "done"]
        recent = [a for a in done if _age(a.last, now) <= DONE_WINDOW][-DONE_CAP:]
        keep = {id(a) for a in recent}
        shown = [a for a in agents if a.state != "done" or id(a) in keep]
        return shown, len(done) - len(recent)
