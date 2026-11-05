"""Periods (spec Sec 11): a store query scoped by `period` accepts a month
'YYYY-MM' or a day 'YYYY-MM-DD' and prefix-matches `day`; calls carry a local
`hour` so the rollup can be read per hour for a day period."""
import sqlite3
from datetime import datetime
from pathlib import Path

import usage.store as store_mod
from tests.helpers import assistant_msg, attachment_msg, hook_context_attachment, iso, user_msg
from usage.parser import FileState, parse_records
from usage.store import Store, local_day, local_hour


def mem() -> Store:
    return Store(Path(":memory:"))


def _hour(ts: str) -> int:
    return datetime.fromisoformat(ts).astimezone().hour


def test_local_hour_is_the_machine_local_hour_or_none():
    ts = "2026-09-14T08:59:05.765Z"
    assert local_hour(ts) == _hour(ts)
    assert local_hour("x") is None and local_hour("") is None and local_hour(None) is None


def _two_days(s: Store) -> None:
    # day A: two calls in the same hour and one two hours later; day B: one call
    s.upsert_chunk(parse_records([user_msg("q", iso(0), cwd="/x/repo"),
                                  assistant_msg("m1", iso(1), cache_read=100, cwd="/x/repo"),
                                  assistant_msg("m2", iso(2), cache_read=200, cwd="/x/repo"),
                                  assistant_msg("m3", iso(125), cache_read=400, cwd="/x/repo")], "s1"))
    s.upsert_chunk(parse_records([user_msg("q", iso(24 * 60), cwd="/y/other"),
                                  assistant_msg("m4", iso(24 * 60 + 1), cache_read=800, cwd="/y/other")], "s2"))
    s.recompute(threshold=1000)


def test_rollup_groups_by_day_hour_project_model_and_a_day_period_selects_one_day():
    s = mem()
    _two_days(s)
    day_a, day_b = local_day(iso(1)), local_day(iso(24 * 60 + 1))
    month = day_a[:7]
    rows = s.rollup(month)
    assert [(r["day"], r["hour"], r["project"], r["calls"], r["cache_read_tokens"]) for r in rows] == [
        (day_a, _hour(iso(1)), "repo", 2, 300),
        (day_a, _hour(iso(125)), "repo", 1, 400),
        (day_b, _hour(iso(24 * 60 + 1)), "other", 1, 800),
    ]
    assert [r["day"] for r in s.rollup(day_a)] == [day_a, day_a]
    assert [r["day"] for r in s.rollup(day_b)] == [day_b]
    assert s.rollup("1999-01-01") == []


def test_sessions_costs_and_skills_accept_a_day_period():
    s = mem()
    _two_days(s)
    day_a, day_b = local_day(iso(1)), local_day(iso(24 * 60 + 1))
    s.upsert_chunk(parse_records([attachment_msg(hook_context_attachment("x" * 400), iso(3), session_id="s1"),
                                  assistant_msg("m5", iso(4), cache_read=100)], "s1"))
    s.set_daily_costs([(day_a, "*", 2.0), (day_b, "*", 3.0)])
    s.set_allocated_costs([(day_a, "repo", "s1", 2.0), (day_b, "other", "s2", 3.0)])

    assert [r["session_id"] for r in s.sessions(day_a)] == ["s1"]
    assert [r["session_id"] for r in s.sessions(day_b)] == ["s2"]
    assert sorted(r["session_id"] for r in s.sessions(day_a[:7])) == ["s1", "s2"]
    assert s.daily_costs(day_a) == {day_a: {"*": 2.0}}
    assert s.allocated_by_project(day_b) == {"other": 3.0}
    assert [r["name"] for r in s.skill_usage(day_a)] == ["Hook context"]
    assert s.skill_usage(day_b) == []


def test_days_with_data_lists_the_days_of_a_month_in_order():
    s = mem()
    _two_days(s)
    day_a, day_b = local_day(iso(1)), local_day(iso(24 * 60 + 1))
    assert s.days_with_data(day_a[:7]) == sorted({day_a, day_b})
    assert s.days_with_data("1999-01") == []


def test_v3_store_migrates_to_v4_stamping_hours_and_forcing_a_full_reparse(tmp_path):
    db_path = tmp_path / "v3.db"
    raw = sqlite3.connect(str(db_path))
    schema_v3 = store_mod.SCHEMA.replace("ts TEXT, day TEXT, hour INTEGER, model TEXT,", "ts TEXT, day TEXT, model TEXT,")
    assert schema_v3 != store_mod.SCHEMA
    schema_v3 = schema_v3.replace("CREATE TABLE IF NOT EXISTS rollup(\n  day TEXT NOT NULL, hour INTEGER,",
                                  "CREATE TABLE IF NOT EXISTS daily(\n  day TEXT NOT NULL,")
    schema_v3 = schema_v3.replace("PRIMARY KEY(day, hour, project, model)", "PRIMARY KEY(day, project, model)")
    raw.executescript(schema_v3)
    raw.execute("INSERT INTO sessions(session_id, first_ts, turns) VALUES('A', ?, 7)", (iso(0),))
    raw.execute("INSERT INTO calls(session_id,message_id,ts,day,model,input_tokens,cache_write,cache_read,output,"
                "thinking,sidechain) VALUES('A','m1',?,?,'x',0,0,0,0,0,0)", (iso(0), iso(0)[:10]))
    raw.execute("INSERT INTO files(path,size,mtime,offset,last_call_ts) VALUES('/a.jsonl', 10, 1.0, 10, NULL)")
    raw.execute("INSERT INTO meta(key,value) VALUES('schema_version','3')")
    raw.commit(); raw.close()

    s = Store(db_path)
    assert s.meta_get("schema_version") == store_mod.SCHEMA_VERSION == "4"
    assert "daily" not in {r["name"] for r in s.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert s.conn.execute("SELECT hour FROM calls WHERE message_id='m1'").fetchone()["hour"] == _hour(iso(0))
    assert s.file_states() == {}
    assert s.session("A")["turns"] == 0
    s2 = Store(db_path)
    assert s2.meta_get("schema_version") == "4"
    s2.save_file_states({"/b.jsonl": FileState("/b.jsonl", 1, 1.0, 1, None)})
