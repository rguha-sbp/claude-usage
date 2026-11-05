"""Sub-agents of a live session (spec Sec 13.2).

A session's sub-agents sit beside its transcript: `<transcript without
.jsonl>/subagents/agent-<id>.jsonl`, each with an `agent-<id>.meta.json`
naming its type, task, depth and the parent's Agent tool_use id.
`AgentReader` reads each agent transcript, and the lead's own, incrementally
(only the bytes appended since the last call, the parser's append-only
assumption, in bounded steps) and keeps running totals, so the Office's
5-second poll stays cheap however long an agent runs. Of the lead's
transcript it keeps only the open tool calls. No database and no knowledge of the
office here.
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
READ_BYTES = 1 << 20                     # how much of a transcript one read step holds


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


def _blocks(rec: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """The content blocks of type `kind` in one record's message."""
    content = _as_list(_as_dict(rec.get("message")).get("content"))
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind]


@dataclass
class _Calls:
    """The tool calls of one transcript still waiting on their tool_result.
    That is all a lead's transcript is read for: whether it still waits on
    the Agent call that spawned one of its agents."""
    size: int = 0                                  # bytes consumed; stops before a cut last line
    open: set[str] = field(default_factory=set)

    def fold(self, rec: dict[str, Any]) -> None:
        # parallel tool calls answer one by one: only the matching call closes
        if rec.get("type") == "user":
            self.open.difference_update(_as_str(b.get("tool_use_id")) or "" for b in _blocks(rec, "tool_result"))
        elif rec.get("type") == "assistant":
            self.open.update(tid for b in _blocks(rec, "tool_use") if (tid := _as_str(b.get("id"))))


@dataclass
class _Tally(_Calls):
    """Running totals for one agent transcript."""
    model: str = ""
    usage: dict[str, dict[str, int]] = field(default_factory=dict)   # message id -> per-field max
    tool_mix: dict[str, int] = field(default_factory=dict)
    spawned: list[str] = field(default_factory=list)                 # Agent/Task tool_use ids
    doing: dict[str, str] | None = None
    handed_back: bool = False
    replied_last: bool = False
    mid_turn: bool = False      # the model owes more: its last message has no stop_reason yet
    started: str | None = None
    last: str | None = None

    @property
    def tokens(self) -> int:
        return sum(u["input_tokens"] + u["cache_write"] + u["cache_read"] + u["output"] for u in self.usage.values())

    def fold(self, rec: dict[str, Any]) -> None:
        super().fold(rec)
        ts = _as_str(rec.get("timestamp"))
        if ts:
            self.started = self.started or ts
            self.last = ts
        message = _as_dict(rec.get("message"))
        if rec.get("type") == "user":
            self.mid_turn = False
            return
        if rec.get("type") != "assistant" or not message:
            return
        # Claude Code writes one record per content block and sets
        # `stop_reason` on the last block of each message only. A trailing
        # tool_result is not counted: an agent killed right after one would
        # look busy for TOOL_PATIENCE instead of asleep (spec Sec 14)
        self.mid_turn = message.get("stop_reason") is None
        self.model = _as_str(message.get("model")) or self.model
        mid = _as_str(message.get("id")) or _as_str(rec.get("uuid"))
        usage = _usage_from(message)
        if mid and usage is not None:
            seen = self.usage.setdefault(mid, dict.fromkeys(_USAGE_FIELDS, 0))
            for name in _USAGE_FIELDS:
                seen[name] = max(seen[name], usage[name])
        for b in _blocks(rec, "tool_use"):
            name, tid = _as_str(b.get("name")) or "?", _as_str(b.get("id"))
            if name == HANDBACK:
                self.handed_back = True
                continue
            self.tool_mix[name] = self.tool_mix.get(name, 0) + 1
            if name in SPAWN_TOOLS and tid:
                self.spawned.append(tid)
        doing = doing_of(message.get("content"))
        if doing is not None:
            self.doing = doing
            self.replied_last = doing["tool"] == "replying"


def _advance[T: _Calls](t: T, path: Path) -> T | None:
    """`t` with whatever was appended since `t.size` folded in, read
    READ_BYTES at a time so a long transcript's first read stays small. Only
    the unfinished last line is held between blocks, so one line longer than
    READ_BYTES is held whole until its newline arrives. A file that shrank
    was rewritten and is read again from the start. None when the file is
    unreadable."""
    try:
        size = path.stat().st_size
        if size < t.size:
            t = type(t)()
        if size == t.size:
            return t
        with open(path, "rb") as fh:
            fh.seek(t.size)
            cut = bytearray()                      # a cut last line waits for the next block or read
            while block := fh.read(READ_BYTES):
                end = block.rfind(b"\n")
                if end < 0:                        # only the new block is searched: no rescan of `cut`
                    cut += block
                    continue
                lines = (cut + block[:end]).split(b"\n")
                cut = bytearray(block[end + 1:])
                for line in lines:
                    t.size += len(line) + 1
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        t.fold(rec)
    except OSError:
        return None
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


def _state(t: _Tally, now: datetime, working: timedelta, awaited: bool) -> str:
    """Done once handed back, or for a legacy agent once its text reply has
    stood QUIET_REPLY and its parent has the result. Running while it wrote
    something within `working` (the leads' own window), or, up to
    TOOL_PATIENCE, while it is busy: one of its tool calls waits on a
    result, its parent's call (`awaited`) still waits on it, or its last
    message is still streaming (`mid_turn`). The model writes nothing while
    it composes a block, so a quiet file alone is not idleness. Asleep
    otherwise, like a quiet lead (spec Sec 13.2)."""
    quiet = _age(t.last, now)
    if t.handed_back or (t.replied_last and not awaited and quiet >= QUIET_REPLY):
        return "done"
    busy = awaited or bool(t.open) or t.mid_turn
    return "running" if quiet <= working or (busy and quiet <= TOOL_PATIENCE) else "asleep"


def _agent(agent_id: str, meta: dict[str, Any], t: _Tally, owner: dict[str, str], awaited: set[str],
           now: datetime, working: timedelta) -> SubAgent:
    depth = meta.get("spawnDepth")
    tool_use_id = _as_str(meta.get("toolUseId")) or ""
    return SubAgent(agent_id=agent_id, type=_as_str(meta.get("agentType")) or "agent",
                    description=_as_str(meta.get("description")) or "",
                    background=meta.get("requestShape") == "background",
                    depth=depth if isinstance(depth, int) and depth > 0 else 1,
                    parent=owner.get(tool_use_id),
                    model=t.model, tokens=t.tokens, tools=sum(t.tool_mix.values()), tool_mix=dict(t.tool_mix),
                    doing=t.doing, started=t.started or "", last=t.last or "", state=_state(t, now, working, tool_use_id in awaited))


@dataclass
class _Session:
    """What the reader remembers of one session between polls."""
    lead: _Calls = field(default_factory=_Calls)
    agents: dict[Path, _Tally] = field(default_factory=dict)   # agent transcript -> its tally


class AgentReader:
    """One per Office; remembers, per session folder, the lead's open calls
    and a `_Tally` per agent transcript between calls."""

    def __init__(self, claude_dir: Path, working_seconds: int = 60) -> None:
        self.claude_dir = claude_dir
        self.working = timedelta(seconds=working_seconds)
        self._sessions: dict[Path, _Session] = {}

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
        """Forget every session whose folder is not in `session_dirs`."""
        self._sessions = {d: s for d, s in self._sessions.items() if d in session_dirs}

    def session_agents(self, transcript_path: Path, now: datetime) -> tuple[list[SubAgent], int]:
        """Running and asleep agents plus the done ones from the last
        DONE_WINDOW (the DONE_CAP most recent), ordered by start; and how
        many done ones were left out."""
        session_dir = self.session_dir(transcript_path)
        folder = session_dir / "subagents" if session_dir is not None else None
        if folder is None or not folder.is_dir():
            return [], 0
        session = self._sessions.setdefault(session_dir, _Session())
        found = []
        for meta_path in sorted(folder.glob("agent-*.meta.json")):
            agent_id = meta_path.name[len("agent-"):-len(".meta.json")]
            jsonl = folder / ("agent-%s.jsonl" % agent_id)
            meta = _read_meta(meta_path)
            if meta is None or not jsonl.is_file():
                continue
            tally = _advance(session.agents.get(jsonl, _Tally()), jsonl)
            if tally is None:
                continue
            session.agents[jsonl] = tally
            if tally.started is not None:
                found.append((agent_id, meta, tally))
        # a parent (the lead or an agent) with the spawning call still open
        # is blocked on that agent: a foreground agent that has not finished
        session.lead = _advance(session.lead, transcript_path.resolve()) or session.lead
        awaited = session.lead.open.union(*(t.open for _, _, t in found))
        owner = {tid: aid for aid, _, t in found for tid in t.spawned}
        agents = sorted((_agent(aid, meta, t, owner, awaited, now, self.working) for aid, meta, t in found),
                        key=lambda a: a.started)
        done = [a for a in agents if a.state == "done"]
        recent = [a for a in done if _age(a.last, now) <= DONE_WINDOW][-DONE_CAP:]
        keep = {id(a) for a in recent}
        shown = [a for a in agents if a.state != "done" or id(a) in keep]
        return shown, len(done) - len(recent)
