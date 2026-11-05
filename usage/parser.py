"""Transcript parsing (spec §4.1). Part 1: records of one transcript chunk.
Part 2: file discovery, incremental offsets and subagent attribution."""
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

COLD_GAP_MINUTES = 60
FIRST_PROMPT_CHARS = 80
_USAGE_FIELDS = ("input_tokens", "cache_write", "cache_read", "output", "thinking")
MAX_FILE_WARNINGS = 20


# --- Leaf-value coercion helpers --------------------------------------------
# A malformed transcript record must never crash parse_records (spec §6). Every
# value read from a record that is indexed, hashed, cast, or has a method
# called on it must go through one of these helpers (or an isinstance check)
# instead of relying on truthiness (`x or default`) or a bare cast (`int(x)`),
# both of which raise on the wrong input type.

def _as_int(v: Any) -> int:
    """int/float -> int; bool -> 0 (never 1); numeric str -> int; non-finite
    (NaN/Infinity, however spelled) -> 0; anything else -> 0. Never raises:
    json parses the literal tokens NaN/Infinity/-Infinity, so these reach the
    parser from real transcript files, both as native floats and as strings
    like "inf"/"Infinity"/"NaN"."""
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return 0
        try:
            return int(v)
        except (ValueError, OverflowError, TypeError):
            return 0
    if isinstance(v, str):
        try:
            return int(v)
        except (ValueError, OverflowError, TypeError):
            try:
                f = float(v)
                if not math.isfinite(f):
                    return 0
                return int(f)
            except (ValueError, OverflowError, TypeError):
                return 0
    return 0


def _as_str(v: Any) -> Optional[str]:
    """Returns v only when it is a non-empty str, else None."""
    return v if isinstance(v, str) and v != "" else None


def _as_dict(v: Any) -> dict:
    """v if it is a dict, else {}."""
    return v if isinstance(v, dict) else {}


def _as_list(v: Any) -> list:
    """v if it is a list, else []."""
    return v if isinstance(v, list) else []


def parse_ts(ts: str) -> datetime:
    s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_parse_ts(ts: str) -> Optional[datetime]:
    """parse_ts, but a str that is not valid ISO-8601 is treated as missing
    rather than crashing parse_records (a non-empty str is not guaranteed to
    be a well-formed timestamp)."""
    try:
        return parse_ts(ts)
    except (ValueError, TypeError):
        return None


def derive_project(cwd: str) -> str:
    marker = "/.claude/worktrees/"
    if marker in cwd:
        cwd = cwd.split(marker, 1)[0]
    name = PurePosixPath(cwd).name
    return name or cwd


@dataclass
class Call:
    message_id: str
    ts: str
    model: str
    input_tokens: int
    cache_write: int
    cache_read: int
    output: int
    thinking: int
    sidechain: bool = False
    # Which transcript file the call came from: "" for the session's main
    # transcript, the subagent file's stem (e.g. "agent-3f2a...") otherwise.
    # Lets the store count what one transcript's context carried without
    # mixing in sibling subagents, which share the same session id.
    agent_id: str = ""

    @property
    def context(self) -> int:
        return self.input_tokens + self.cache_write + self.cache_read

    @property
    def total_tokens(self) -> int:
        return self.context + self.output


@dataclass
class SkillLoad:
    """One injection of reusable text into a session's context: a Skill
    tool load (kind "skill", load_id = the loading call's message id), a
    hook's additionalContext (kind "hook") or the skill listing menu (kind
    "listing"); for the latter two load_id is the attachment record's uuid
    and body_bytes is exact. A skill load's body_bytes is None here: the
    body arrives as a separate `invoked_skills` attachment (recent Claude
    Code versions only) and is kept per skill name in SessionAgg.skill_bodies;
    older transcripts fall back to a context-delta estimate in the store."""
    load_id: str
    name: str
    kind: str
    ts: str
    sidechain: bool = False
    body_bytes: Optional[int] = None
    agent_id: str = ""  # same meaning as Call.agent_id


@dataclass
class SessionAgg:
    session_id: str
    title: Optional[str] = None
    first_prompt: Optional[str] = None
    project: Optional[str] = None
    cwd: Optional[str] = None
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    turns: int = 0
    tool_calls: int = 0
    compactions: int = 0
    tool_result_bytes: int = 0
    content_bytes: int = 0
    cold_restarts: int = 0
    rebuild_tokens: int = 0
    subagent_tokens: int = 0
    last_call_ts: Optional[str] = None
    read_counts: Dict[str, int] = field(default_factory=dict)
    calls: List[Call] = field(default_factory=list)
    skill_loads: List[SkillLoad] = field(default_factory=list)
    skill_bodies: Dict[str, int] = field(default_factory=dict)  # skill name -> exact body bytes
    compactions_at: List[Tuple[str, str]] = field(default_factory=list)  # (agent_id, ts) of each compact summary

    def display_title(self) -> str:
        return self.title or self.first_prompt or self.session_id

    def merge_from(self, other: "SessionAgg") -> None:
        if self.title is None:
            self.title = other.title
        if self.first_prompt is None:
            self.first_prompt = other.first_prompt
        if self.cwd is None:
            self.cwd, self.project = other.cwd, other.project
        if other.first_ts and (self.first_ts is None or other.first_ts < self.first_ts):
            self.first_ts = other.first_ts
        if other.last_ts and (self.last_ts is None or other.last_ts > self.last_ts):
            self.last_ts = other.last_ts
        if other.last_call_ts and (self.last_call_ts is None or other.last_call_ts > self.last_call_ts):
            self.last_call_ts = other.last_call_ts
        for name in ("turns", "tool_calls", "compactions", "tool_result_bytes", "content_bytes",
                     "cold_restarts", "rebuild_tokens", "subagent_tokens"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for path, n in other.read_counts.items():
            self.read_counts[path] = self.read_counts.get(path, 0) + n
        self.calls.extend(other.calls)
        self.skill_loads.extend(other.skill_loads)
        for name, n in other.skill_bodies.items():
            self.skill_bodies[name] = max(self.skill_bodies.get(name, 0), n)
        self.compactions_at.extend(other.compactions_at)


def _usage_from(message: dict) -> Optional[Dict[str, int]]:
    u = message.get("usage")
    # If usage is present but not a dict at all, the record is malformed; signal with None
    # (parse_records skips the record rather than guessing at a shape).
    if u is not None and not isinstance(u, dict):
        return None
    u = _as_dict(u)
    details = u.get("output_tokens_details")
    if details is not None and not isinstance(details, dict):
        return None
    details = _as_dict(details)
    # Every individual leaf value is coerced safely: a valid usage dict may
    # still carry a malformed leaf (e.g. a non-numeric string), which must
    # not crash the whole record.
    return {
        "input_tokens": _as_int(u.get("input_tokens")),
        "cache_write": _as_int(u.get("cache_creation_input_tokens")),
        "cache_read": _as_int(u.get("cache_read_input_tokens")),
        "output": _as_int(u.get("output_tokens")),
        "thinking": _as_int(details.get("thinking_tokens")),
    }


def _content_bytes(content) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    return len(json.dumps(content).encode("utf-8"))


def _is_tool_result(content) -> bool:
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = _as_str(b.get("text"))
                if t:
                    parts.append(t)
        return " ".join(parts)
    return ""


def parse_records(records: Iterable[dict], session_id: str,
                  prev_last_call_ts: Optional[str] = None,
                  sidechain: bool = False, agent_id: Optional[str] = None) -> SessionAgg:
    """`agent_id`: the subagent transcript's file stem, None/"" for the
    session's main transcript (see Call.agent_id)."""
    agg = SessionAgg(session_id=session_id)
    aid = agent_id or ""
    merged: Dict[str, Call] = {}
    order: List[str] = []

    # Track max content_bytes and unique read paths per message_id
    msg_content_bytes: Dict[str, int] = {}
    msg_read_paths: Dict[str, Dict[str, bool]] = {}  # message_id -> {file_path: True}
    seen_skill_loads: Set[Tuple[str, str]] = set()  # (message_id, skill) -- streaming snapshots repeat blocks
    last_hook_event: Optional[str] = None  # hookName of the latest hook_success record, e.g. "SessionStart:startup"

    for rec in records:
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
        # timestamp must be a str for parse_ts; anything else is treated as missing.
        ts = _as_str(rec.get("timestamp"))
        if ts:
            if agg.first_ts is None or ts < agg.first_ts:
                agg.first_ts = ts
            if agg.last_ts is None or ts > agg.last_ts:
                agg.last_ts = ts
        # cwd: non-str is ignored rather than propagated to derive_project.
        cwd = _as_str(rec.get("cwd"))
        if cwd and agg.cwd is None:
            agg.cwd = cwd
            agg.project = derive_project(cwd)
        if rtype == "ai-title":
            title = _as_str(rec.get("aiTitle"))
            if title:
                agg.title = title
            continue
        if rtype == "attachment":
            att = rec.get("attachment")
            if not isinstance(att, dict):
                continue
            atype = att.get("type")
            load_id = _as_str(rec.get("uuid"))
            if atype == "hook_success":
                last_hook_event = _as_str(att.get("hookName")) or _as_str(att.get("hookEvent"))
            elif atype == "invoked_skills":
                for sk in _as_list(att.get("skills")):
                    if not isinstance(sk, dict):
                        continue
                    name, body = _as_str(sk.get("name")), sk.get("content")
                    if name and isinstance(body, str):
                        agg.skill_bodies[name] = max(agg.skill_bodies.get(name, 0), len(body))
            elif atype in ("hook_additional_context", "skill_listing") and load_id:
                content = att.get("content")
                if isinstance(content, list):
                    size = sum(len(x) for x in content if isinstance(x, str))
                elif isinstance(content, str):
                    size = len(content)
                else:
                    continue
                if atype == "skill_listing":
                    name, kind = "Skill listing", "listing"
                else:
                    event = (last_hook_event or "").split(":", 1)[0]
                    name, kind = ("%s hook" % event if event else "Hook context"), "hook"
                agg.skill_loads.append(SkillLoad(load_id, name, kind, ts or "",
                                                 sidechain=bool(sidechain or rec.get("isSidechain")),
                                                 body_bytes=size, agent_id=aid))
            continue
        if rtype == "user":
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            # Treat non-list/non-str content as empty
            if content is not None and not isinstance(content, (str, list)):
                content = None
            agg.content_bytes += _content_bytes(content)
            if _is_tool_result(content):
                agg.tool_calls += 1
                agg.tool_result_bytes += _content_bytes(content)
            else:
                agg.turns += 1
                if rec.get("isCompactSummary"):
                    agg.compactions += 1
                    agg.compactions_at.append((aid, ts or ""))
                elif agg.first_prompt is None:
                    text = _text_of(content).strip()
                    if text:
                        agg.first_prompt = text[:FIRST_PROMPT_CHARS]
            continue
        if rtype == "assistant":
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            # message.id / uuid must be a non-empty str; a record where neither
            # is must be skipped rather than used as an unhashable dict key.
            mid = _as_str(message.get("id")) or _as_str(rec.get("uuid")) or ""
            if not mid:
                continue
            content = message.get("content")

            # Track max content_bytes per message_id (not sum of duplicates)
            content_sz = _content_bytes(content)
            if mid not in msg_content_bytes:
                msg_content_bytes[mid] = content_sz
            else:
                msg_content_bytes[mid] = max(msg_content_bytes[mid], content_sz)

            # Track unique read paths per message_id
            if mid not in msg_read_paths:
                msg_read_paths[mid] = {}
            for block in _as_list(content):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") == "Skill":
                    inp = block.get("input")
                    skill = _as_str(inp.get("skill")) if isinstance(inp, dict) else None
                    if skill and (mid, skill) not in seen_skill_loads:
                        seen_skill_loads.add((mid, skill))
                        agg.skill_loads.append(SkillLoad(mid, skill, "skill", ts or "",
                                                         sidechain=bool(sidechain or rec.get("isSidechain")),
                                                         agent_id=aid))
                    continue
                if block.get("name") == "Read":
                    inp = block.get("input")
                    # file_path is counted only when it is a non-empty str;
                    # unhashable/wrong-typed values must not crash or be
                    # used as a dict key.
                    fp = _as_str(inp.get("file_path")) if isinstance(inp, dict) else None
                    if fp:
                        msg_read_paths[mid][fp] = True  # Mark path as seen for this msg_id

            usage = _usage_from(message)
            if usage is None:
                # Skip records with malformed nested fields
                continue
            if mid in merged:
                c = merged[mid]
                for name in _USAGE_FIELDS:
                    setattr(c, name, max(getattr(c, name), usage[name]))
            else:
                model = _as_str(message.get("model")) or "unknown"
                merged[mid] = Call(message_id=mid, ts=ts or "", model=model,
                                   sidechain=bool(sidechain or rec.get("isSidechain")), agent_id=aid, **usage)
                order.append(mid)
            continue

    # Add accumulated content_bytes and read_counts (per-message_id max and union)
    for mid in order:
        if mid in msg_content_bytes:
            agg.content_bytes += msg_content_bytes[mid]
        if mid in msg_read_paths:
            for fp in msg_read_paths[mid]:
                agg.read_counts[fp] = agg.read_counts.get(fp, 0) + 1

    prev_ts = prev_last_call_ts
    for mid in order:
        call = merged[mid]
        agg.calls.append(call)
        if call.sidechain:
            agg.subagent_tokens += call.total_tokens
        elif call.ts:
            if prev_ts:
                t1, t0 = _safe_parse_ts(call.ts), _safe_parse_ts(prev_ts)
                if t1 is not None and t0 is not None:
                    gap = (t1 - t0).total_seconds() / 60.0
                    if gap > COLD_GAP_MINUTES:
                        agg.cold_restarts += 1
                        agg.rebuild_tokens += call.cache_write
            prev_ts = call.ts
        # Only non-sidechain calls update last_call_ts
        if not call.sidechain and call.ts and (agg.last_call_ts is None or call.ts > agg.last_call_ts):
            agg.last_call_ts = call.ts
    return agg


# --- Part 2: discovery, incremental offsets, subagent attribution ----------

@dataclass
class TranscriptFile:
    path: Path
    session_id: str
    parent_session_id: Optional[str]  # set for subagent transcripts


@dataclass
class FileState:
    path: str
    size: int
    mtime: float
    offset: int
    last_call_ts: Optional[str] = None


@dataclass
class ChunkResult:
    agg: SessionAgg
    new_offset: int
    warnings: List[str]


@dataclass
class ParseResult:
    chunks: Dict[str, SessionAgg]          # keyed by (parent) session id; only sessions with new data
    file_states: Dict[str, FileState]      # updated states for every file read this round
    warnings: List[str]
    present_paths: Set[str]                # every transcript path that exists now


def discover(claude_dir: Path) -> List[TranscriptFile]:
    projects = claude_dir / "projects"
    out: List[TranscriptFile] = []
    if not projects.is_dir():
        return out
    for project_dir in sorted(p for p in projects.iterdir() if p.is_dir()):
        for f in sorted(project_dir.glob("*.jsonl")):
            if not f.is_file():
                continue  # e.g. a directory that happens to be named "*.jsonl"
            out.append(TranscriptFile(path=f, session_id=f.stem, parent_session_id=None))
        for f in sorted(project_dir.glob("*/subagents/*.jsonl")):
            if not f.is_file():
                continue
            parent = f.parent.parent.name
            out.append(TranscriptFile(path=f, session_id=f.stem, parent_session_id=parent))
    return out


def _peek_first_ts(path: Path) -> Optional[str]:
    """Best-effort timestamp of a transcript file's first record, read
    without parsing the whole file. Used only to order transcript files
    deterministically before feeding them to the store (see
    parse_incremental) -- any error (unreadable file, malformed first line,
    missing/non-str `timestamp`) simply yields None so the caller falls
    back to the file's mtime instead."""
    try:
        with path.open("rb") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return None
                return _as_str(obj.get("timestamp")) if isinstance(obj, dict) else None
    except OSError:
        return None
    return None


def _discovery_sort_key(tf: "TranscriptFile") -> Tuple[float, float, int, str]:
    """Sort key for ordering discovered transcript files before they are
    parsed and fed to the store: earliest first-record timestamp first,
    falling back to file mtime when no usable timestamp is found. File
    mtime is also used as a tie-break when two files' first-record
    timestamps are equal -- notably when a resumed/forked session's
    transcript is a byte-for-byte copy of an earlier session's history
    (plus new records): both files' first record carries the *same*
    original timestamp, but the fork's file is created on disk strictly
    later than the original's, which mtime captures and a timestamp tie
    cannot. A main transcript is ordered before its own subagent
    transcripts on a full tie. This makes which session "wins" Store's
    global first-seen-wins call dedupe (see store.py) depend on the
    sessions' actual chronology rather than on discovery order
    (project/filename sort), which is what let a resumed/forked session's
    copied history double-count (see fix report)."""
    ts = _peek_first_ts(tf.path)
    epoch = None
    if ts:
        dt = _safe_parse_ts(ts)
        if dt is not None:
            epoch = dt.timestamp()
    try:
        mtime = tf.path.stat().st_mtime
    except OSError:
        mtime = float("inf")
    if epoch is None:
        epoch = mtime
    return (epoch, mtime, 1 if tf.parent_session_id is not None else 0, str(tf.path))


def parse_file(path: Path, session_id: str, offset: int = 0,
               prev_last_call_ts: Optional[str] = None, sidechain: bool = False,
               agent_id: Optional[str] = None) -> ChunkResult:
    """Read complete lines from `path` starting at `offset`. A line that is
    not valid JSON, or that parses to something other than a JSON object, is
    never allowed to raise: it is skipped and recorded in warnings (spec §6),
    capped at MAX_FILE_WARNINGS entries per file plus a final "...N more"
    summary so one badly corrupted file cannot flood the warnings list."""
    records: List[dict] = []
    warnings: List[str] = []
    overflow = 0
    consumed = offset
    with path.open("rb") as fh:
        fh.seek(offset)
        lineno = 0
        while True:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break  # partial trailing line: leave for next round
            lineno += 1
            consumed += len(line)
            stripped = line.strip()
            if not stripped:
                continue
            bad_reason = None
            try:
                obj = json.loads(stripped.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                bad_reason = "invalid JSON"
            else:
                if isinstance(obj, dict):
                    records.append(obj)
                else:
                    bad_reason = "invalid JSON (not an object)"
            if bad_reason is not None:
                if len(warnings) < MAX_FILE_WARNINGS:
                    warnings.append("%s:%d: %s" % (path, lineno, bad_reason))
                else:
                    overflow += 1
    if overflow:
        warnings.append("…%d more" % overflow)
    agg = parse_records(records, session_id, prev_last_call_ts=prev_last_call_ts, sidechain=sidechain,
                        agent_id=agent_id)
    return ChunkResult(agg=agg, new_offset=consumed, warnings=warnings)


def parse_incremental(claude_dir: Path, states: Dict[str, FileState]) -> ParseResult:
    chunks: Dict[str, SessionAgg] = {}
    new_states: Dict[str, FileState] = {}
    warnings: List[str] = []
    present: Set[str] = set()
    # Sorted by chronology (see _discovery_sort_key), not discover()'s
    # project/filename order, so ParseResult.chunks -- built below by first
    # occurrence of each owner session -- is populated in a deterministic,
    # timestamp-driven order for pipeline.refresh to feed into the store.
    for tf in sorted(discover(claude_dir), key=_discovery_sort_key):
        key = str(tf.path)
        present.add(key)
        try:
            stat = tf.path.stat()
        except OSError:
            continue
        prior = states.get(key)
        offset, prev_ts = 0, None
        if prior is not None:
            if stat.st_size == prior.size and stat.st_mtime == prior.mtime:
                continue
            if stat.st_size >= prior.size and stat.st_mtime >= prior.mtime:
                offset, prev_ts = prior.offset, prior.last_call_ts
        owner = tf.parent_session_id or tf.session_id
        try:
            res = parse_file(tf.path, owner, offset=offset, prev_last_call_ts=prev_ts,
                             sidechain=tf.parent_session_id is not None,
                             agent_id=tf.path.stem if tf.parent_session_id is not None else None)
        except OSError as exc:
            # Same treatment as the stat() failure above: an unreadable file
            # (e.g. permission denied) must not abort the whole round.
            warnings.append("%s: %s" % (key, exc))
            continue
        warnings.extend(res.warnings)
        log.debug("%s: read from offset %d, %d byte(s), %d call(s), %d skill load(s)", key, offset,
                  res.new_offset - offset, len(res.agg.calls), len(res.agg.skill_loads))
        last_call = res.agg.last_call_ts or (prev_ts if offset else None)
        new_states[key] = FileState(path=key, size=stat.st_size, mtime=stat.st_mtime,
                                    offset=res.new_offset, last_call_ts=last_call)
        if owner in chunks:
            chunks[owner].merge_from(res.agg)
        else:
            chunks[owner] = res.agg
    return ParseResult(chunks=chunks, file_states=new_states, warnings=warnings, present_paths=present)
