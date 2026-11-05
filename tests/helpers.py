"""Builders for synthetic Claude Code transcript records used in tests."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

BASE_TS = "2026-09-01T09:00:00.000Z"
_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _FMT).replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def iso(minutes: int = 0, base: str = BASE_TS) -> str:
    return _fmt(_parse(base) + timedelta(minutes=minutes))


def _common(ts: str, session_id: str, cwd: str) -> dict:
    return {"uuid": str(uuid.uuid4()), "timestamp": ts, "sessionId": session_id,
            "cwd": cwd, "version": "2.1.300", "gitBranch": "main"}


def user_msg(text: str, ts: str, session_id: str = "s1", cwd: str = "/Users/x/proj") -> dict:
    rec = _common(ts, session_id, cwd)
    rec.update({"type": "user", "message": {"role": "user", "content": text}})
    return rec


def tool_result_msg(result_text: str, ts: str, session_id: str = "s1",
                    cwd: str = "/Users/x/proj") -> dict:
    rec = _common(ts, session_id, cwd)
    rec.update({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": result_text}]}})
    return rec


def compact_summary(ts: str, session_id: str = "s1") -> dict:
    rec = user_msg("This session is being continued from a previous conversation.", ts, session_id)
    rec["isCompactSummary"] = True
    return rec


def assistant_msg(msg_id: str, ts: str, model: str = "claude-fable-5", input_tokens: int = 2,
                  cache_write: int = 0, cache_read: int = 0, output: int = 10, thinking: int = 0,
                  content: Optional[list] = None, session_id: str = "s1",
                  cwd: str = "/Users/x/proj", sidechain: bool = False) -> dict:
    rec = _common(ts, session_id, cwd)
    rec.update({
        "type": "assistant",
        "isSidechain": sidechain,
        "requestId": "req_" + msg_id,
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": model,
            "content": content if content is not None else [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_write,
                "cache_read_input_tokens": cache_read,
                "output_tokens": output,
                "output_tokens_details": {"thinking_tokens": thinking},
            },
        },
    })
    return rec


def read_block(file_path: str, tool_id: str = "t1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": file_path}}


def ai_title(title: str, session_id: str = "s1") -> dict:
    return {"type": "ai-title", "aiTitle": title, "sessionId": session_id}


def write_jsonl(path: Path, records: Iterable[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def make_project(root: Path, slug: str) -> Path:
    p = root / "projects" / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def make_session(root: Path, slug: str, session_id: str, records: Iterable[dict],
                 subagents: Optional[Dict[str, Iterable[dict]]] = None) -> Path:
    proj = make_project(root, slug)
    main = write_jsonl(proj / ("%s.jsonl" % session_id), records)
    for name, recs in (subagents or {}).items():
        write_jsonl(proj / session_id / "subagents" / ("%s.jsonl" % name), recs)
    return main


def skill_block(skill: str, tool_id: str = "sk1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill}}


def attachment_msg(attachment: dict, ts: str, session_id: str = "s1",
                   cwd: str = "/Users/x/proj") -> dict:
    rec = _common(ts, session_id, cwd)
    rec.update({"type": "attachment", "isSidechain": False, "attachment": attachment})
    return rec


def invoked_skills_attachment(bodies: Dict[str, str]) -> dict:
    return {"type": "invoked_skills",
            "skills": [{"name": n, "path": "plugin:" + n, "content": c} for n, c in bodies.items()]}


def hook_context_attachment(text: str) -> dict:
    return {"type": "hook_additional_context", "content": [text]}


def skill_listing_attachment(text: str) -> dict:
    return {"type": "skill_listing", "content": text, "skillCount": 3, "isInitial": True, "names": []}
