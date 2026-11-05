"""Assemble the /api/summary payload."""
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .projection import compute_period, kind_of, prev_period
from .skills import aggregate_loads, context_tokens_of
from .store import Store, UNKNOWN_PROJECT
from .tips import month_tips, session_tips


def abbreviate_path(path: Optional[str]) -> str:
    """Replaces the user's home directory prefix with '~'; keeps the rest
    of the path unchanged. Returns '' for a missing/empty path."""
    if not path:
        return ""
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


def serialize_session(sess: Dict[str, Any], threshold: int,
                      skill_loads: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """`skill_loads`: this session's rows from Store.skill_usage (any month);
    they become `skills` (one row per skill/injection, heaviest first) and
    `skills_carried_tokens`, which the skill-heavy tip reads."""
    out = dict(sess)
    out["display_title"] = sess.get("title") or sess.get("first_prompt") or sess["session_id"]
    out["display_cwd"] = abbreviate_path(sess.get("cwd"))
    out["skills"] = aggregate_loads(skill_loads or [], context_tokens_of(sess))
    out["skills_carried_tokens"] = sum(r["carried_tokens"] for r in out["skills"])
    out["tips"] = [asdict(t) for t in session_tips(out, threshold)]
    out["peak_over"] = (sess.get("peak_context") or 0) > threshold
    return out


def _projects(store: Store, sessions: List[Dict[str, Any]], period: str, cost_available: bool) -> List[Dict[str, Any]]:
    """One row per project for `period`. `sessions` must already be ordered
    most-recent-first (as `Store.sessions()` returns them) so that the first
    session encountered for a project is the one whose cwd is recorded."""
    groups: Dict[str, Dict[str, Any]] = {}
    for s in sessions:
        name = s.get("project") or UNKNOWN_PROJECT
        g = groups.setdefault(name, {"project": name, "cwd": abbreviate_path(s.get("cwd")),
                                     "sessions": 0, "tokens": 0, "over": 0, "ctx": 0})
        g["sessions"] += 1
        g["tokens"] += s["total_tokens"]
        g["over"] += s.get("tokens_over_threshold") or 0
        g["ctx"] += s["input_tokens"] + s["cache_write_tokens"] + s["cache_read_tokens"]
    alloc = store.allocated_by_project(period) if cost_available else {}
    prev_alloc = store.allocated_by_project(prev_period(period)) if cost_available else {}
    prev_tokens: Dict[str, int] = {}
    if not cost_available:
        for s in store.sessions(prev_period(period)):
            key = s.get("project") or UNKNOWN_PROJECT
            prev_tokens[key] = prev_tokens.get(key, 0) + s["total_tokens"]
    out = []
    for name, g in groups.items():
        value = alloc.get(name, 0.0) if cost_available else float(g["tokens"])
        previous = prev_alloc.get(name) if cost_available else prev_tokens.get(name)
        out.append({"project": name, "cwd": g["cwd"], "sessions": g["sessions"], "value": value,
                    "previous_value": previous, "over_share": (g["over"] / g["ctx"]) if g["ctx"] else None})
    out.sort(key=lambda p: -p["value"])
    return out


def board_stats(store: Store, day: str, cost_available: bool) -> Dict[str, Any]:
    """Live numbers for the Office notice board (spec Sec 12.7): what `day`
    has cost so far and which project carries most of it. Falls back to
    tokens when ccusage is unavailable, exactly as the rest of the page does."""
    rows = store.rollup(day)
    tokens = sum(r["input_tokens"] + r["cache_write_tokens"] + r["cache_read_tokens"] + r["output_tokens"]
                 for r in rows)
    if cost_available:
        value = store.daily_costs(day).get(day, {}).get("*", 0.0)
        by_project = store.allocated_by_project(day)
    else:
        value = float(tokens)
        by_project = {}
        for r in rows:
            n = r["input_tokens"] + r["cache_write_tokens"] + r["cache_read_tokens"] + r["output_tokens"]
            by_project[r["project"]] = by_project.get(r["project"], 0.0) + n
    top = max(by_project.items(), key=lambda kv: kv[1]) if by_project else None
    return {"day": day, "unit": "usd" if cost_available else "tokens", "value": value, "tokens": tokens,
            "top_project": top[0] if top else None, "top_value": top[1] if top else None}


def period_phrase(period: str, today: date) -> str:
    """How the page refers to the period in running text: 'this month',
    'today' or 'on this day'."""
    if kind_of(period) == "month":
        return "this month"
    return "today" if period == today.isoformat() else "on this day"


def build_summary(store: Store, cfg: Config, period: Optional[str], today: date,
                  warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """`period` is a month 'YYYY-MM' or a day 'YYYY-MM-DD' (spec Sec 11);
    None means the current month, or the latest month with data."""
    months = store.months_with_data()
    if period is None:
        current = today.strftime("%Y-%m")
        period = current if current in months or not months else months[-1]
    phrase = period_phrase(period, today)
    cost_available = store.meta_get("cost_available") == "1"
    cost_error = store.meta_get("cost_error", "") or ""
    threshold = cfg.threshold_tokens
    m = compute_period(store, period, today, threshold, cost_available)
    raw_sessions = store.sessions(period)
    loads = store.skill_usage(period)
    loads_by_session: Dict[str, List[Dict[str, Any]]] = {}
    for l in loads:
        loads_by_session.setdefault(l["session_id"], []).append(l)
    sessions = [serialize_session(s, threshold, loads_by_session.get(s["session_id"])) for s in raw_sessions]
    skills = aggregate_loads(loads, sum(context_tokens_of(s) for s in raw_sessions))
    return {
        "period": period,
        "kind": m.kind,
        "period_phrase": phrase,
        "month": m.month,
        "months": months,
        "days": store.days_with_data(m.month),
        "unit": m.unit,
        "cost_available": cost_available,
        "cost_error": cost_error,
        "threshold": threshold,
        "generated_at": store.meta_get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "warnings": list(warnings or []),
        "summary": {
            "to_date": m.to_date, "projected": m.projected, "pace": m.pace,
            "days_elapsed": m.days_elapsed, "days_in_period": m.days_in_period,
            "previous_period": m.previous_period, "previous_total": m.previous_total,
            "previous_days": m.previous_days,
            "spend_trend": asdict(m.spend_trend), "over_value": m.over_value, "over_share": m.over_share,
            "previous_over_share": m.previous_over_share, "over_trend": asdict(m.over_trend),
            "top_session": m.top_session,
        },
        "series": [asdict(pt) for pt in m.series],
        "model_split": m.model_split,
        "token_split": m.token_split,
        "sessions": sessions,
        "projects": _projects(store, raw_sessions, period, cost_available),
        "skills": skills,
        "skills_note": ("Skill bodies, hook context and the skill listing are sized from the transcript at "
                        "4 bytes per token; a skill whose text was not recorded is estimated from the context "
                        "growth on the next call. Carried tokens count that text re-read on every later call."),
        "month_tips": [asdict(t) for t in month_tips(sessions, threshold, cost_available, skills, phrase)],
        "cost_note": (
            "Dollar figures from ccusage at list price; per-project figures are allocated from "
            "session costs by weighted tokens per day."
            if cost_available else
            "Dollar figures unavailable: ccusage did not run (%s). Showing tokens." % cost_error
        ),
    }
