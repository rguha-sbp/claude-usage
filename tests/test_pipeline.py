import json
import os
import time
from datetime import date
from pathlib import Path

from tests.helpers import ai_title, assistant_msg, iso, make_session, user_msg
from usage.config import load_config
from usage.pipeline import refresh
from usage.store import Store, local_day
from usage.summary import abbreviate_path, build_summary, serialize_session

SESSION_JSON = json.dumps({"session": [{"period": "s1", "totalCost": 8.0, "modelBreakdowns": []}]})


def cfg_for(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                             "threshold_tokens": 1000, "ccusage_command": ["ccusage"]}))
    return load_config(p)


def ok_runner(day):
    daily = json.dumps({"daily": [{"period": day, "totalCost": 8.0, "modelBreakdowns": [{"modelName": "claude-fable-5", "cost": 8.0}]}]})
    return lambda cmd: (0, SESSION_JSON if "session" in cmd else daily, "")


def test_refresh_end_to_end_and_incremental(tmp_path):
    cfg = cfg_for(tmp_path)
    p = make_session(tmp_path, "-p", "s1", [ai_title("Title"), user_msg("hi", iso(0), cwd="/x/repo"),
                                            assistant_msg("m1", iso(1), cache_read=5000, cwd="/x/repo")])
    store = Store(cfg.db_path)
    warnings = refresh(store, cfg, ok_runner(local_day(iso(1))))
    assert warnings == []
    row = store.session("s1")
    assert row["title"] == "Title" and row["calls"] == 1 and row["calls_over_threshold"] == 1
    assert row["cost"] == 8.0 and store.meta_get("cost_available") == "1"
    with p.open("a") as fh:
        fh.write(json.dumps(assistant_msg("m2", iso(2), cache_read=10)) + "\n")
    refresh(store, cfg, ok_runner(local_day(iso(1))))
    assert store.session("s1")["calls"] == 2


def test_refresh_orders_chunks_so_forked_session_history_is_not_double_counted(tmp_path):
    # A resumed/forked session's transcript is a copy of an earlier
    # session's history (identical message ids, identical timestamps,
    # since the record content is literally copied) plus new records
    # appended under the new session id (see fix report). The dedupe
    # winner must be the ORIGINAL session, not whichever transcript file
    # happens to sort first alphabetically or get discovered first.
    cfg = cfg_for(tmp_path)
    shared = [user_msg("hi", iso(0)), assistant_msg("m1", iso(1), cache_read=1000),
             assistant_msg("m2", iso(2), cache_read=2000)]
    # "aaa-fork" sorts alphabetically *before* "zzz-original" -- proving
    # the winner isn't decided by discovery/filename order.
    original = make_session(tmp_path, "-p", "zzz-original",
                            shared + [assistant_msg("m3", iso(3), cache_read=3000)])
    fork = make_session(tmp_path, "-p", "aaa-fork",
                        shared + [assistant_msg("m4", iso(4), cache_read=4000)])
    # Both files' first record shares the exact same timestamp (iso(0)),
    # since it was literally copied -- a real tie the fix's mtime
    # tie-break must resolve. Force the fork's file to look newer on disk,
    # as a real fork's transcript file (created later) would be, even
    # though its copied records carry the original's old timestamps.
    now = time.time()
    os.utime(str(original), (now - 100, now - 100))
    os.utime(str(fork), (now, now))

    store = Store(cfg.db_path)
    refresh(store, cfg, lambda cmd: (1, "", "no ccusage"))

    orig_row = store.session("zzz-original")
    fork_row = store.session("aaa-fork")
    assert orig_row["calls"] == 3            # m1, m2, m3: shared history claimed by the original
    assert fork_row["calls"] == 1            # m4 only: its own new record


def test_refresh_end_to_end_global_dedupe_matches_ccusage_style_unique_totals(tmp_path):
    # End-to-end: recompute()'s totals across the pair of sessions equal
    # the ccusage-style global dedupe (sum over *unique* message ids), not
    # naive per-session sums which would double-count the shared history.
    cfg = cfg_for(tmp_path)
    shared = [assistant_msg("m1", iso(1), cache_read=1000, output=10),
             assistant_msg("m2", iso(2), cache_read=2000, output=20)]
    original = make_session(tmp_path, "-p", "session-a", shared)
    fork = make_session(tmp_path, "-p", "session-b",
                        shared + [assistant_msg("m3", iso(3), cache_read=3000, output=30)])
    now = time.time()
    os.utime(str(original), (now - 100, now - 100))
    os.utime(str(fork), (now, now))

    store = Store(cfg.db_path)
    refresh(store, cfg, lambda cmd: (1, "", "no ccusage"))

    a = store.session("session-a")
    b = store.session("session-b")
    assert a["calls"] + b["calls"] == 3                          # m1, m2, m3: 3 unique message ids
    assert a["cache_read_tokens"] + b["cache_read_tokens"] == 1000 + 2000 + 3000
    assert a["output_tokens"] + b["output_tokens"] == 10 + 20 + 30


def test_refresh_records_cost_failure_as_warning(tmp_path):
    cfg = cfg_for(tmp_path)
    make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0))])
    store = Store(cfg.db_path)
    warnings = refresh(store, cfg, lambda cmd: (1, "", "no ccusage"))
    assert any("no ccusage" in w for w in warnings)
    assert store.meta_get("cost_available") == "0"


def test_abbreviate_path():
    assert abbreviate_path(str(Path.home() / "Code" / "x")) == "~/Code/x"
    assert abbreviate_path(None) == ""


def test_build_summary_shape(tmp_path):
    cfg = cfg_for(tmp_path)
    make_session(tmp_path, "-p", "s1", [ai_title("Title"), user_msg("hi", iso(0), cwd="/x/repo"),
                                        assistant_msg("m1", iso(1), cache_read=5000, cwd="/x/repo")])
    store = Store(cfg.db_path)
    day = local_day(iso(1))
    refresh(store, cfg, ok_runner(day))
    s = build_summary(store, cfg, None, date.fromisoformat(day))
    assert s["month"] == day[:7] and s["months"] == [day[:7]]
    assert s["period"] == day[:7] and s["kind"] == "month" and s["days"] == [day]
    assert s["period_phrase"] == "this month"
    assert s["cost_available"] is True and s["unit"] == "usd" and s["threshold"] == 1000
    assert s["summary"]["to_date"] == 8.0
    assert s["summary"]["top_session"]["title"] == "Title"
    assert s["sessions"][0]["display_title"] == "Title" and s["sessions"][0]["peak_over"] is True
    assert s["sessions"][0]["tips"][0]["id"] == "compact-earlier"
    assert s["projects"][0]["project"] == "repo" and round(s["projects"][0]["value"], 6) == 8.0
    assert s["month_tips"][0]["id"] == "month-compact"
    assert len(s["series"]) >= 28
    assert s["generated_at"]


def test_build_summary_for_a_day_period(tmp_path):
    cfg = cfg_for(tmp_path)
    make_session(tmp_path, "-p", "s1", [ai_title("Title"), user_msg("hi", iso(0), cwd="/x/repo"),
                                        assistant_msg("m1", iso(1), cache_read=5000, cwd="/x/repo")])
    make_session(tmp_path, "-p", "s2", [user_msg("hi", iso(24 * 60), cwd="/x/repo"),
                                        assistant_msg("m2", iso(24 * 60 + 1), cache_read=50, cwd="/x/repo")])
    store = Store(cfg.db_path)
    day, other = local_day(iso(1)), local_day(iso(24 * 60 + 1))
    refresh(store, cfg, ok_runner(day))
    s = build_summary(store, cfg, day, date.fromisoformat(day))
    assert s["period"] == day and s["kind"] == "day" and s["month"] == day[:7]
    assert s["days"] == [day, other] and s["period_phrase"] == "today"
    assert [x["session_id"] for x in s["sessions"]] == ["s1"]
    assert len(s["series"]) == 24 and s["summary"]["days_in_period"] == 1
    assert s["summary"]["to_date"] == 8.0
    assert s["month_tips"][0]["id"] == "month-compact" and "today" in s["month_tips"][0]["detail"]
    later = build_summary(store, cfg, day, date.fromisoformat(other))
    assert later["period_phrase"] == "on this day"


def test_build_summary_without_costs(tmp_path):
    cfg = cfg_for(tmp_path)
    make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0)), assistant_msg("m1", iso(1), cache_read=50)])
    store = Store(cfg.db_path)
    refresh(store, cfg, lambda cmd: (1, "", "missing"))
    day = local_day(iso(1))
    s = build_summary(store, cfg, day[:7], date.fromisoformat(day))
    assert s["cost_available"] is False and s["unit"] == "tokens"
    assert s["sessions"][0]["cost"] is None
    assert "missing" in s["cost_error"]


def test_refresh_scopes_subagent_skill_listings_to_their_own_transcript(tmp_path):
    from tests.helpers import attachment_msg, skill_listing_attachment
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                                "ccusage_command": ["ccusage"], "refresh_seconds": 3600}))
    cfg = load_config(cfgp)
    listing = skill_listing_attachment("L" * 8_000)
    make_session(tmp_path, "-proj", "s1", [
        attachment_msg(listing, iso(0)),
        user_msg("hi", iso(0)),
        assistant_msg("m1", iso(1), cache_read=5_000, output=10),
        assistant_msg("m2", iso(9), cache_read=6_000, output=10),
    ], subagents={
        "agent-a": [attachment_msg(listing, iso(2)), assistant_msg("a1", iso(3), sidechain=True),
                    assistant_msg("a2", iso(4), sidechain=True), assistant_msg("a3", iso(5), sidechain=True)],
        "agent-b": [attachment_msg(listing, iso(6)), assistant_msg("b1", iso(7), sidechain=True)],
    })
    store = Store(cfg.db_path)
    refresh(store, cfg, runner=lambda cmd: (1, "", "no ccusage"))
    rows = sorted((r["ts"], r["calls_carried"], r["injected_tokens"]) for r in store.skill_usage(iso(0)[:7]))
    assert rows == [(iso(0), 2, 2_000), (iso(2), 3, 2_000), (iso(6), 1, 2_000)]
