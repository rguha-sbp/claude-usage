"""Skills panel: fold per-load rows from `Store.skill_usage` into one row per
skill / injection (spec addendum 2026-09-09, "high token usage skills").

A load's cost is not the text injected once but that text re-read on every
later call of the session (cache reads are billed): carried_tokens is
sum(injected_tokens x calls_carried) over the measured loads. `share` is that
against the context tokens of whatever scope the rows describe (the month, or
one session), None when the scope has no context tokens.
"""
from statistics import median
from typing import Any, Dict, List, Optional


def aggregate_loads(loads: List[Dict[str, Any]], context_tokens: int) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for l in loads:
        g = groups.setdefault(l["name"], {"name": l["name"], "kind": l["kind"], "loads": 0, "_sessions": set(),
                                          "_measured": [], "_exact": True, "carried_tokens": 0})
        g["loads"] += 1
        g["_sessions"].add(l["session_id"])
        tokens = l.get("injected_tokens")
        if tokens is not None:
            g["_measured"].append(tokens)
            g["carried_tokens"] += tokens * (l.get("calls_carried") or 0)
        g["_exact"] = g["_exact"] and bool(l.get("exact"))
    out: List[Dict[str, Any]] = []
    for g in groups.values():
        measured = g.pop("_measured")
        injected: Optional[int] = int(median(measured)) if measured else None
        share: Optional[float] = (g["carried_tokens"] / context_tokens) if context_tokens else None
        out.append({"name": g["name"], "kind": g["kind"], "loads": g["loads"], "sessions": len(g.pop("_sessions")),
                    "injected_tokens": injected, "exact": g.pop("_exact") and bool(measured),
                    "carried_tokens": g["carried_tokens"], "share": share})
    out.sort(key=lambda r: (-r["carried_tokens"], r["name"]))
    return out


def context_tokens_of(sess: Dict[str, Any]) -> int:
    return (sess.get("input_tokens") or 0) + (sess.get("cache_write_tokens") or 0) + (sess.get("cache_read_tokens") or 0)
