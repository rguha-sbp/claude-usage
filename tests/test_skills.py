"""Aggregation of skill loads into panel rows, and their place in the summary payload."""
import json
from datetime import date

from tests.helpers import (
    assistant_msg, attachment_msg, hook_context_attachment, iso, make_session, skill_block, user_msg,
)
from usage.config import load_config
from usage.pipeline import refresh
from usage.skills import aggregate_loads
from usage.store import Store
from usage.summary import build_summary


def _load(name, session, tokens, carried, kind="skill", exact=False):
    return {"session_id": session, "name": name, "kind": kind, "ts": iso(0),
            "injected_tokens": tokens, "exact": exact, "calls_carried": carried}


def test_aggregate_groups_by_name_and_sorts_by_carried_tokens():
    rows = aggregate_loads([
        _load("a", "s1", 5_000, 100), _load("a", "s2", 6_000, 10), _load("a", "s2", None, 3),
        _load("Skill listing", "s1", 1_000, 100, kind="listing", exact=True),
    ], context_tokens=2_000_000)
    assert [r["name"] for r in rows] == ["a", "Skill listing"]
    a = rows[0]
    assert a["kind"] == "skill" and a["loads"] == 3 and a["sessions"] == 2
    assert a["injected_tokens"] == 5_500          # median of the measured loads
    assert a["exact"] is False
    assert a["carried_tokens"] == 5_000 * 100 + 6_000 * 10
    assert a["share"] == (5_000 * 100 + 6_000 * 10) / 2_000_000
    assert rows[1]["exact"] is True and rows[1]["share"] == 0.05


def test_aggregate_with_nothing_measured_reports_none():
    rows = aggregate_loads([_load("a", "s1", None, 0)], context_tokens=0)
    assert rows == [{"name": "a", "kind": "skill", "loads": 1, "sessions": 1, "injected_tokens": None,
                     "exact": False, "carried_tokens": 0, "share": None}]


def _summary(tmp_path):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                                "ccusage_command": ["ccusage"], "refresh_seconds": 3600}))
    cfg = load_config(cfgp)
    make_session(tmp_path, "-proj", "s1", [
        attachment_msg({"type": "hook_success", "hookName": "SessionStart:startup"}, iso(0)),
        attachment_msg(hook_context_attachment("h" * 4_000), iso(0)),
        user_msg("hello", iso(0)),
        assistant_msg("m1", iso(1), cache_read=10_000, output=100, content=[skill_block("superpowers:brainstorming")]),
        assistant_msg("m2", iso(2), cache_read=16_100, output=100),
        assistant_msg("m3", iso(3), cache_read=16_300, output=100),
    ])
    store = Store(cfg.db_path)
    refresh(store, cfg, runner=lambda cmd: (1, "", "no ccusage"))
    return build_summary(store, cfg, None, date(2026, 9, 8))


def test_summary_carries_month_skills_and_per_session_skills(tmp_path):
    payload = _summary(tmp_path)
    by_name = {r["name"]: r for r in payload["skills"]}
    assert by_name["superpowers:brainstorming"]["injected_tokens"] == 6_000
    assert by_name["superpowers:brainstorming"]["carried_tokens"] == 12_000
    assert by_name["SessionStart hook"]["kind"] == "hook" and by_name["SessionStart hook"]["carried_tokens"] == 3_000
    sess = payload["sessions"][0]
    assert [r["name"] for r in sess["skills"]] == ["superpowers:brainstorming", "SessionStart hook"]
    assert sess["skills_carried_tokens"] == 15_000
    assert "skills_note" in payload and "4 bytes" in payload["skills_note"]
