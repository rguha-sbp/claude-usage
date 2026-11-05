import sqlite3
from pathlib import Path

from tests.helpers import ai_title, assistant_msg, iso, tool_result_msg, user_msg
from usage.parser import FileState, parse_records
from usage.store import Store, local_day
import usage.store as store_mod


def mem() -> Store:
    return Store(Path(":memory:"))


def test_local_day_formats_date():
    assert len(local_day("2026-09-01T09:00:00.000Z")) == 10


def test_file_states_roundtrip():
    s = mem()
    s.save_file_states({"/a.jsonl": FileState("/a.jsonl", 10, 1.5, 10, iso(1))})
    got = s.file_states()
    assert got["/a.jsonl"].offset == 10 and got["/a.jsonl"].last_call_ts == iso(1)


def test_save_file_states_merges_without_replacing_table():
    s = mem()
    s.save_file_states({"/a.jsonl": FileState("/a.jsonl", 10, 1.5, 10, iso(1))})
    s.save_file_states({"/b.jsonl": FileState("/b.jsonl", 20, 2.5, 20, iso(2))})
    got = s.file_states()
    assert set(got.keys()) == {"/a.jsonl", "/b.jsonl"}
    assert got["/a.jsonl"].offset == 10


def test_upsert_chunk_is_additive_and_calls_take_max():
    s = mem()
    a1 = parse_records([ai_title("T"), user_msg("hi", iso(0)),
                        assistant_msg("m1", iso(1), cache_read=1000, output=5)], "s1")
    s.upsert_chunk(a1)
    a2 = parse_records([tool_result_msg("x" * 50, iso(2)),
                        assistant_msg("m1", iso(1), cache_read=1000, output=90),   # later snapshot of m1
                        assistant_msg("m2", iso(3), cache_read=3000, output=7)], "s1")
    s.upsert_chunk(a2)
    s.recompute(threshold=2000)
    row = s.session("s1")
    assert row["title"] == "T" and row["turns"] == 1 and row["tool_calls"] == 1
    assert row["calls"] == 2
    assert row["output_tokens"] == 97           # max(5, 90) + 7
    assert row["cache_read_tokens"] == 4000
    assert row["peak_context"] == 3002
    assert row["calls_over_threshold"] == 1 and row["tokens_over_threshold"] == 1002
    assert row["models"] == {"claude-fable-5": 2}
    assert row["total_tokens"] == 4 + 4000 + 97


def test_upsert_chunk_keeps_existing_title_project_cwd_when_already_set():
    s = mem()
    a1 = parse_records([ai_title("T"), user_msg("hi", iso(0), cwd="/x/repo")], "s1")
    s.upsert_chunk(a1)
    a2 = parse_records([ai_title("Other"), user_msg("bye", iso(1), cwd="/y/other")], "s1")
    s.upsert_chunk(a2)
    row = s.session("s1")
    assert row["title"] == "T"
    assert row["cwd"] == "/x/repo"
    assert row["project"] == "repo"


def test_recompute_excludes_sidechain_from_threshold_but_counts_tokens():
    s = mem()
    a = parse_records([assistant_msg("m1", iso(0), cache_read=100),
                       assistant_msg("x1", iso(1), cache_read=500000, output=10, sidechain=True)],
                      "s1", sidechain=False)
    a.calls[1].sidechain = True
    s.upsert_chunk(a)
    s.recompute(threshold=200000)
    row = s.session("s1")
    assert row["calls_over_threshold"] == 0 and row["peak_context"] == 102
    assert row["cache_read_tokens"] == 500100


def test_rollup_rebuild_groups_by_day_project_model():
    s = mem()
    a = parse_records([user_msg("hi", iso(0), cwd="/x/repo"),
                       assistant_msg("m1", iso(1), model="a", cache_read=100, cwd="/x/repo"),
                       assistant_msg("m2", iso(2), model="b", cache_read=300, cwd="/x/repo"),
                       assistant_msg("m3", iso(24 * 60), model="a", cache_read=5000, cwd="/x/repo")], "s1")
    s.upsert_chunk(a)
    s.recompute(threshold=1000)
    rows = s.rollup(local_day(iso(0))[:7])
    assert len(rows) == 3
    d1a = [r for r in rows if r["model"] == "a" and r["day"] == local_day(iso(1))][0]
    assert d1a["project"] == "repo" and d1a["calls"] == 1 and d1a["cache_read_tokens"] == 100
    d2a = [r for r in rows if r["day"] == local_day(iso(24 * 60))][0]
    assert d2a["calls_over_threshold"] == 1 and d2a["tokens_over_threshold"] == 4002


def test_costs_and_session_listing_and_months():
    s = mem()
    s.upsert_chunk(parse_records([user_msg("a", iso(0)), assistant_msg("m1", iso(1), cache_read=10)], "s1"))
    s.upsert_chunk(parse_records([user_msg("b", iso(5)), assistant_msg("m2", iso(6), cache_read=10)], "s2"))
    s.recompute(threshold=1000)
    s.set_session_costs([("s1", 1.5, {"claude-fable-5": 1.5})])
    s.set_daily_costs([(local_day(iso(0)), "*", 2.0), (local_day(iso(0)), "claude-fable-5", 2.0)])
    s.set_allocated_costs([(local_day(iso(0)), "proj", "s1", 1.5), (local_day(iso(0)), "proj", "s2", 0.5)])
    month = local_day(iso(0))[:7]
    rows = s.sessions(month)
    assert [r["session_id"] for r in rows] == ["s2", "s1"]     # newest first
    assert rows[1]["cost"] == 1.5 and rows[0]["cost"] is None
    assert s.daily_costs(month)[local_day(iso(0))]["*"] == 2.0
    assert s.allocated_by_project(month) == {"proj": 2.0}
    assert s.months_with_data() == [month]
    assert s.sessions("1999-01") == []


def test_session_days_weighted_tokens():
    s = mem()
    s.upsert_chunk(parse_records([assistant_msg("m1", iso(0), input_tokens=0, cache_write=100, cache_read=1000, output=10)], "s1"))
    s.recompute(threshold=1000)
    days = s.session_days("s1")
    assert days == [(local_day(iso(0)), 100 * 1.25 + 1000 * 0.1 + 10 * 5)]


def test_mark_sources_flags_missing_transcripts():
    s = mem()
    s.upsert_chunk(parse_records([user_msg("a", iso(0))], "s1"))
    s.upsert_chunk(parse_records([user_msg("b", iso(0))], "s2"))
    s.recompute(threshold=1000)
    s.mark_sources({"/p/projects/x/s1.jsonl"})
    assert s.session("s1")["source_present"] is True
    assert s.session("s2")["source_present"] is False


def test_meta_roundtrip():
    s = mem()
    assert s.meta_get("k") is None and s.meta_get("k", "d") == "d"
    s.meta_set("k", "v")
    assert s.meta_get("k") == "v"


def test_local_day_returns_none_for_unparsable_or_missing_ts():
    assert local_day("x") is None
    assert local_day("") is None
    assert local_day(None) is None


def test_upsert_chunk_tolerates_bad_call_ts_and_excludes_it_from_rollup():
    s = mem()
    a = parse_records([assistant_msg("m1", iso(0)), assistant_msg("m2", iso(1))], "s1")
    a.calls[0].ts = "x"   # simulate the malformed-but-tolerated timestamp parse_records allows
    s.upsert_chunk(a)
    s.recompute(threshold=1000)
    row = s.session("s1")
    assert row["calls"] == 2
    rows = s.rollup(local_day(iso(1))[:7])
    assert len(rows) == 1


def test_upsert_chunk_dedupes_shared_message_id_first_upserted_session_wins():
    # A resumed/forked session copies earlier history into a new transcript
    # with its own session id, so both sessions' chunks carry the same
    # assistant message_id(s) for the shared history (see fix report). The
    # Store rule: a non-sidechain message_id may be counted under exactly
    # one session, globally -- whichever chunk is upserted first claims it.
    s = mem()
    a = parse_records([assistant_msg("m1", iso(0)), assistant_msg("m2", iso(1))], "A")
    b = parse_records([assistant_msg("m1", iso(0)), assistant_msg("m3", iso(2))], "B")
    s.upsert_chunk(a)
    s.upsert_chunk(b)
    s.recompute(threshold=1_000_000)
    assert s.session("A")["calls"] == 2       # m1, m2: both claimed by A
    assert s.session("B")["calls"] == 1       # m3 only: m1 already claimed by A


def test_upsert_chunk_dedupe_winner_is_purely_insertion_order_not_session_identity():
    # Same two chunks as above, upserted in the opposite order: the Store
    # itself has no notion of "the original session" (no first_ts
    # comparison) -- it is purely first-write-wins on the message_id. This
    # is exactly why pipeline.refresh must order chunks by first_ts before
    # calling upsert_chunk (tested separately in tests/test_pipeline.py).
    s = mem()
    a = parse_records([assistant_msg("m1", iso(0)), assistant_msg("m2", iso(1))], "A")
    b = parse_records([assistant_msg("m1", iso(0)), assistant_msg("m3", iso(2))], "B")
    s.upsert_chunk(b)
    s.upsert_chunk(a)
    s.recompute(threshold=1_000_000)
    assert s.session("B")["calls"] == 2       # m1, m3: B was upserted first
    assert s.session("A")["calls"] == 1       # m2 only: m1 already claimed by B


def test_upsert_chunk_keeps_sidechain_call_colliding_with_main_id_in_other_session():
    s = mem()
    a = parse_records([assistant_msg("m1", iso(0))], "A")
    b = parse_records([assistant_msg("m1", iso(0))], "B", sidechain=True)
    s.upsert_chunk(a)
    s.upsert_chunk(b)
    s.recompute(threshold=1_000_000)
    assert s.session("A")["calls"] == 1
    assert s.session("B")["calls"] == 1
    rows = s.conn.execute(
        "SELECT session_id, sidechain FROM calls WHERE message_id='m1' ORDER BY session_id").fetchall()
    assert [(r["session_id"], r["sidechain"]) for r in rows] == [("A", 0), ("B", 1)]


def test_store_migrates_pre_existing_cross_session_duplicate_message_ids(tmp_path):
    # Simulate a DB written before this fix: no schema_version, no unique
    # index, and a duplicate non-sidechain message_id already sitting under
    # two different sessions (SQLite cannot add a UNIQUE index over
    # existing duplicates, so Store must resolve them first).
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(store_mod.SCHEMA)
    raw.execute("INSERT INTO sessions(session_id, first_ts) VALUES('A', ?)", (iso(0),))
    raw.execute("INSERT INTO sessions(session_id, first_ts) VALUES('B', ?)", (iso(5),))
    row_sql = ("INSERT INTO calls(session_id,message_id,ts,day,model,input_tokens,cache_write,"
              "cache_read,output,thinking,sidechain) VALUES(?,?,?,?,?,0,0,0,0,0,0)")
    raw.execute(row_sql, ("A", "m1", iso(0), local_day(iso(0)), "x"))   # earlier session
    raw.execute(row_sql, ("B", "m1", iso(5), local_day(iso(5)), "x"))   # duplicate, later session
    raw.execute(row_sql, ("B", "m2", iso(6), local_day(iso(6)), "x"))   # unique to B, no conflict
    raw.commit()
    raw.close()

    s = Store(db_path)
    rows = s.conn.execute("SELECT session_id FROM calls WHERE message_id='m1'").fetchall()
    assert [r["session_id"] for r in rows] == ["A"]      # earlier first_ts kept, B's dropped
    rows2 = s.conn.execute("SELECT session_id FROM calls WHERE message_id='m2'").fetchall()
    assert [r["session_id"] for r in rows2] == ["B"]
    idx = s.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='calls_msgid_main'").fetchone()
    assert idx is not None
    assert s.meta_get("schema_version") == store_mod.SCHEMA_VERSION

    # Idempotent: re-opening (already migrated) changes nothing further.
    s2 = Store(db_path)
    rows3 = s2.conn.execute("SELECT session_id FROM calls WHERE message_id='m1'").fetchall()
    assert [r["session_id"] for r in rows3] == ["A"]


def test_store_migration_resolves_tie_by_lexicographically_smaller_session_id(tmp_path):
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(store_mod.SCHEMA)
    raw.execute("INSERT INTO sessions(session_id, first_ts) VALUES('zzz', ?)", (iso(0),))
    raw.execute("INSERT INTO sessions(session_id, first_ts) VALUES('aaa', ?)", (iso(0),))
    row_sql = ("INSERT INTO calls(session_id,message_id,ts,day,model,input_tokens,cache_write,"
              "cache_read,output,thinking,sidechain) VALUES(?,?,?,?,?,0,0,0,0,0,0)")
    raw.execute(row_sql, ("zzz", "m1", iso(0), local_day(iso(0)), "x"))
    raw.execute(row_sql, ("aaa", "m1", iso(0), local_day(iso(0)), "x"))
    raw.commit()
    raw.close()

    s = Store(db_path)
    rows = s.conn.execute("SELECT session_id FROM calls WHERE message_id='m1'").fetchall()
    assert [r["session_id"] for r in rows] == ["aaa"]


def test_upsert_chunk_rolls_back_on_error(monkeypatch):
    import usage.store as store_mod
    s = mem()
    s.upsert_chunk(parse_records([assistant_msg("m1", iso(0))], "s1"))

    def boom(ts):
        raise RuntimeError("boom")

    monkeypatch.setattr(store_mod, "local_day", boom)
    try:
        s.upsert_chunk(parse_records([assistant_msg("m2", iso(1))], "s1"))
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
    assert s.conn.in_transaction is False
    monkeypatch.setattr(store_mod, "local_day", local_day)
    s.upsert_chunk(parse_records([assistant_msg("m3", iso(2))], "s1"))
    s.recompute(threshold=1000)
    row = s.session("s1")
    assert row["calls"] == 2   # m1 + m3; m2's failed upsert was fully rolled back
