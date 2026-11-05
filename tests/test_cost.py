import json
import subprocess
from pathlib import Path

import pytest

from tests.helpers import assistant_msg, iso, user_msg
from usage.cost import (CostResult, SessionCost, allocate, apply_costs, blended_price,
                        default_runner, fetch_costs, parse_daily_json, parse_session_json)
from usage.parser import parse_records
from usage.store import UNKNOWN_PROJECT, Store, local_day

SESSION_JSON = json.dumps({"session": [
    {"period": "s1", "totalCost": 12.5, "inputTokens": 10, "cacheCreationTokens": 20, "cacheReadTokens": 30,
     "outputTokens": 40, "totalTokens": 100, "modelsUsed": ["claude-fable-5"],
     "modelBreakdowns": [{"modelName": "claude-fable-5", "cost": 12.5}],
     "metadata": {"lastActivity": "2026-09-02"}},
    {"period": "s2", "totalCost": 0.5, "modelBreakdowns": []},
], "totals": {"totalCost": 13.0}})

DAILY_JSON = json.dumps({"daily": [
    {"period": "2026-09-01", "totalCost": 3.0,
     "modelBreakdowns": [{"modelName": "claude-fable-5", "cost": 2.0}, {"modelName": "claude-opus-5", "cost": 1.0}]},
    {"period": "2026-09-02", "totalCost": 10.0, "modelBreakdowns": [{"modelName": "claude-fable-5", "cost": 10.0}]},
], "totals": {"totalCost": 13.0}})


def test_parse_session_json():
    rows = parse_session_json(SESSION_JSON)
    assert [(r.session_id, r.cost) for r in rows] == [("s1", 12.5), ("s2", 0.5)]
    assert rows[0].model_costs == {"claude-fable-5": 12.5}


def test_parse_daily_json():
    rows = parse_daily_json(DAILY_JSON)
    assert rows[0].day == "2026-09-01" and rows[0].cost == 3.0
    assert rows[0].model_costs == {"claude-fable-5": 2.0, "claude-opus-5": 1.0}


def test_fetch_costs_success_uses_expected_commands():
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return (0, SESSION_JSON if cmd[-3] == "session" else DAILY_JSON, "")

    res = fetch_costs(["ccusage"], runner)
    assert res.available is True and res.error is None
    assert seen == [["ccusage", "session", "--json", "--offline"], ["ccusage", "daily", "--json", "--offline"]]
    assert len(res.sessions) == 2 and len(res.daily) == 2


def test_fetch_costs_failure_modes():
    assert fetch_costs(["x"], lambda c: (1, "", "boom")).available is False
    assert "boom" in fetch_costs(["x"], lambda c: (1, "", "boom")).error
    assert fetch_costs(["x"], lambda c: (0, "not json", "")).available is False

    def missing(cmd):
        raise FileNotFoundError("npx")
    assert fetch_costs(["x"], missing).available is False

    def daily_fails(cmd):
        return (0, SESSION_JSON, "") if "session" in cmd else (2, "", "Unknown option")
    assert fetch_costs(["x"], daily_fails).available is False


def test_allocate_proportional_and_equal_fallback():
    got = allocate(10.0, [("2026-09-01", 1.0), ("2026-09-02", 3.0)])
    assert got == {"2026-09-01": 2.5, "2026-09-02": 7.5}
    assert allocate(4.0, [("a", 0.0), ("b", 0.0)]) == {"a": 2.0, "b": 2.0}
    assert allocate(4.0, []) == {}


def test_apply_costs_writes_all_tables():
    s = Store(Path(":memory:"))
    s.upsert_chunk(parse_records([user_msg("a", iso(0), cwd="/x/repo"),
                                  assistant_msg("m1", iso(1), cache_read=1000, cwd="/x/repo"),
                                  assistant_msg("m2", iso(24 * 60), cache_read=3000, cwd="/x/repo")], "s1"))
    s.recompute(threshold=100000)
    res = fetch_costs(["c"], lambda cmd: (0, SESSION_JSON if "session" in cmd else DAILY_JSON, ""))
    apply_costs(s, res)
    assert s.session("s1")["cost"] == 12.5
    assert s.daily_costs("2026-09")["2026-09-01"] == {"*": 3.0, "claude-fable-5": 2.0, "claude-opus-5": 1.0}
    month = local_day(iso(0))[:7]
    alloc = s.allocated_by_project(month)
    assert round(alloc["repo"], 6) == 12.5
    days = dict(s.session_days("s1"))
    d1, d2 = local_day(iso(1)), local_day(iso(24 * 60))
    rows = {r["day"]: r["cost"] for r in s.conn.execute("SELECT day, cost FROM costs_allocated WHERE session_id='s1'")}
    assert round(rows[d1] / rows[d2], 3) == round(days[d1] / days[d2], 3)


def test_apply_costs_noop_when_unavailable():
    s = Store(Path(":memory:"))
    apply_costs(s, CostResult(available=False, sessions=[], daily=[], error="x"))
    assert s.conn.execute("SELECT COUNT(*) FROM costs_session").fetchone()[0] == 0


def test_blended_price():
    assert blended_price(10.0, 1000) == 0.01
    assert blended_price(None, 1000) is None
    assert blended_price(10.0, 0) is None


def test_unknown_project_sentinel_matches_store_rollup_and_allocated():
    # A session with no derivable project (project=NULL) must land under the
    # same sentinel in both Store.rollup() and costs_allocated, or the
    # Projects tab would show the sentinel's usage and its cost apart.
    s = Store(Path(":memory:"))
    day = local_day(iso(0))
    s.conn.execute("INSERT INTO sessions(session_id, project, first_ts, last_ts) VALUES('s9', NULL, ?, ?)",
                  (iso(0), iso(0)))
    s.conn.execute(
        "INSERT INTO calls(session_id, message_id, ts, day, model, input_tokens, cache_write, cache_read, "
        "output, thinking, sidechain) VALUES('s9','m9',?,?,?,0,0,0,0,0,0)",
        (iso(0), day, "claude-fable-5"))
    s.conn.commit()
    s.recompute(threshold=100000)

    res = CostResult(True, [SessionCost("s9", 5.0, {"claude-fable-5": 5.0})], [], None)
    apply_costs(s, res)

    month = day[:7]
    by_day = {r["day"]: r for r in s.rollup(month)}
    assert by_day[day]["project"] == UNKNOWN_PROJECT == "unknown"
    assert round(s.allocated_by_project(month)[UNKNOWN_PROJECT], 6) == 5.0


def test_parse_session_json_rejects_wrong_top_level_shape():
    with pytest.raises(Exception):
        parse_session_json("[1,2,3]")
    with pytest.raises(Exception):
        parse_session_json("null")


def test_parse_daily_json_rejects_wrong_top_level_shape():
    with pytest.raises(Exception):
        parse_daily_json("[1,2,3]")
    with pytest.raises(Exception):
        parse_daily_json("null")


def test_parse_session_json_skips_non_dict_rows():
    text = json.dumps({"session": ["oops", {"period": "s1", "totalCost": 1.0, "modelBreakdowns": []}]})
    rows = parse_session_json(text)
    assert len(rows) == 1
    assert rows[0].session_id == "s1" and rows[0].cost == 1.0


def test_fetch_costs_wrong_shape_json_is_unavailable_not_a_crash():
    res = fetch_costs(["x"], lambda c: (0, "[1,2,3]", ""))
    assert res.available is False and res.error is not None

    res2 = fetch_costs(["x"], lambda c: (0, "null", ""))
    assert res2.available is False and res2.error is not None


def test_default_runner_never_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr(subprocess, "run", boom)
    rc, out, err = default_runner(["x"])
    assert rc != 0
    assert "nope" in err
