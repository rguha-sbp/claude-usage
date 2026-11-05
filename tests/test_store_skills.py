"""Store persistence and derivation of skill/injection cost (skills panel)."""
from pathlib import Path

from tests.helpers import (
    assistant_msg, attachment_msg, compact_summary, hook_context_attachment, invoked_skills_attachment, iso,
    skill_block, tool_result_msg, user_msg,
)
from usage.parser import parse_records
from usage.store import Store


def mem() -> Store:
    return Store(Path(":memory:"))


def _month(ts: str) -> str:
    return ts[:7]


def test_skill_load_tokens_estimated_from_context_delta_minus_own_output():
    s = mem()
    a = parse_records([
        user_msg("hi", iso(0)),
        # loading call: 10_000 context, 500 output -> the next call should start at 10_500
        assistant_msg("m1", iso(1), cache_read=10_000, output=500, content=[skill_block("superpowers:brainstorming")]),
        tool_result_msg("Launching skill", iso(2)),
        # next call: 16_100 context -> 5_600 tokens of injected text
        assistant_msg("m2", iso(3), cache_read=16_100, output=20),
        assistant_msg("m3", iso(4), cache_read=16_500, output=20),
        assistant_msg("m4", iso(5), cache_read=16_900, output=20),
    ], "s1")
    s.upsert_chunk(a)
    rows = s.skill_usage(_month(iso(0)))
    assert len(rows) == 1
    r = rows[0]
    assert r["session_id"] == "s1" and r["name"] == "superpowers:brainstorming" and r["kind"] == "skill"
    assert r["injected_tokens"] == 5_600 and r["exact"] is False
    assert r["calls_carried"] == 3
    assert r["ts"] == iso(1)


def test_skill_load_tokens_exact_when_body_known_anywhere():
    s = mem()
    body = "x" * 20_000  # -> 5_000 tokens at 4 bytes per token
    # session A (older transcript format): no body attachment
    s.upsert_chunk(parse_records([
        assistant_msg("a1", iso(1), cache_read=1_000, output=10, content=[skill_block("sk")]),
        assistant_msg("a2", iso(2), cache_read=9_000, output=10),
    ], "sA"))
    # session B (new format) carries the body
    s.upsert_chunk(parse_records([
        assistant_msg("b1", iso(10), cache_read=1_000, output=10, content=[skill_block("sk")]),
        attachment_msg(invoked_skills_attachment({"sk": body}), iso(11), session_id="sB"),
        assistant_msg("b2", iso(12), cache_read=6_010, output=10),
    ], "sB"))
    rows = {r["session_id"]: r for r in s.skill_usage(_month(iso(0)))}
    assert rows["sA"]["injected_tokens"] == 5_000 and rows["sA"]["exact"] is True
    assert rows["sB"]["injected_tokens"] == 5_000 and rows["sB"]["exact"] is True


def test_skill_load_estimate_clamps_at_zero_and_is_none_without_next_call():
    s = mem()
    s.upsert_chunk(parse_records([
        assistant_msg("m1", iso(1), cache_read=10_000, output=5_000, content=[skill_block("sk")]),
        assistant_msg("m2", iso(2), cache_read=1_000, output=10),   # compacted in between
        assistant_msg("m3", iso(3), cache_read=1_000, output=10, content=[skill_block("sk2")]),
    ], "s1"))
    rows = {r["name"]: r for r in s.skill_usage(_month(iso(0)))}
    assert rows["sk"]["injected_tokens"] == 0
    assert rows["sk2"]["injected_tokens"] is None and rows["sk2"]["calls_carried"] == 0


def test_next_call_arriving_in_a_later_chunk_completes_the_estimate():
    s = mem()
    s.upsert_chunk(parse_records([
        assistant_msg("m1", iso(1), cache_read=10_000, output=100, content=[skill_block("sk")]),
    ], "s1"))
    assert s.skill_usage(_month(iso(0)))[0]["injected_tokens"] is None
    s.upsert_chunk(parse_records([
        assistant_msg("m2", iso(2), cache_read=13_100, output=10),
        assistant_msg("m3", iso(3), cache_read=13_200, output=10),
    ], "s1"))
    r = s.skill_usage(_month(iso(0)))[0]
    assert r["injected_tokens"] == 3_000 and r["calls_carried"] == 2


def test_reupserting_the_same_chunk_does_not_duplicate_loads():
    s = mem()
    a = parse_records([
        attachment_msg({"type": "hook_success", "hookName": "SessionStart:startup"}, iso(0)),
        attachment_msg(hook_context_attachment("h" * 4_000), iso(0)),
        assistant_msg("m1", iso(1), cache_read=10_000, output=100, content=[skill_block("sk")]),
        assistant_msg("m2", iso(2), cache_read=13_100, output=10),
    ], "s1")
    s.upsert_chunk(a)
    s.upsert_chunk(a)
    rows = s.skill_usage(_month(iso(0)))
    assert sorted((r["name"], r["kind"], r["injected_tokens"], r["exact"], r["calls_carried"]) for r in rows) == [
        ("SessionStart hook", "hook", 1_000, True, 2),
        ("sk", "skill", 3_000, False, 1),
    ]


def test_carried_calls_do_not_count_a_subagents_calls_for_a_main_thread_load():
    s = mem()
    main = parse_records([
        assistant_msg("m1", iso(1), cache_read=1_000, output=10, content=[skill_block("sk")]),
        assistant_msg("m2", iso(2), cache_read=2_010, output=10),
    ], "s1")
    sub = parse_records([
        assistant_msg("x1", iso(3), cache_read=500, output=10),
        assistant_msg("x2", iso(4), cache_read=600, output=10),
    ], "s1", sidechain=True, agent_id="agent-x")
    main.merge_from(sub)
    s.upsert_chunk(main)
    r = s.skill_usage(_month(iso(0)))[0]
    assert r["calls_carried"] == 1 and r["injected_tokens"] == 1_000


def test_skill_usage_is_filtered_by_month_of_the_load():
    s = mem()
    s.upsert_chunk(parse_records([
        assistant_msg("m1", "2026-08-31T10:00:00.000Z", cache_read=1_000, output=10, content=[skill_block("old")]),
        assistant_msg("m2", "2026-09-01T10:00:00.000Z", cache_read=2_000, output=10, content=[skill_block("new")]),
        assistant_msg("m3", "2026-09-01T10:01:00.000Z", cache_read=3_000, output=10),
    ], "s1"))
    assert [r["name"] for r in s.skill_usage("2026-09")] == ["new"]
    assert [r["name"] for r in s.skill_usage("2026-08")] == ["old"]


def test_carried_calls_are_scoped_to_the_loading_transcript():
    s = mem()
    main = parse_records([
        assistant_msg("m1", iso(1), cache_read=1_000, output=10, content=[skill_block("sk")]),
        assistant_msg("m2", iso(2), cache_read=2_010, output=10),
    ], "s1")
    a = parse_records([
        attachment_msg({"type": "skill_listing", "content": "L" * 4_000}, iso(3)),
        assistant_msg("a1", iso(4), cache_read=500, output=10),
        assistant_msg("a2", iso(5), cache_read=600, output=10),
    ], "s1", sidechain=True, agent_id="agent-a")
    b = parse_records([
        attachment_msg({"type": "skill_listing", "content": "L" * 4_000}, iso(6)),
        assistant_msg("b1", iso(7), cache_read=500, output=10),
    ], "s1", sidechain=True, agent_id="agent-b")
    main.merge_from(a)
    main.merge_from(b)
    s.upsert_chunk(main)
    rows = sorted((r["name"], r["ts"], r["calls_carried"]) for r in s.skill_usage(_month(iso(0))))
    assert rows == [("Skill listing", iso(3), 2), ("Skill listing", iso(6), 1), ("sk", iso(1), 1)]


def test_carried_calls_stop_at_the_next_compaction_of_that_transcript():
    s = mem()
    s.upsert_chunk(parse_records([
        assistant_msg("m1", iso(1), cache_read=1_000, output=10, content=[skill_block("sk")]),
        assistant_msg("m2", iso(2), cache_read=2_010, output=10),
        assistant_msg("m3", iso(3), cache_read=2_100, output=10),
        compact_summary(iso(4)),
        assistant_msg("m4", iso(5), cache_read=300, output=10),
        assistant_msg("m5", iso(6), cache_read=400, output=10),
    ], "s1"))
    r = s.skill_usage(_month(iso(0)))[0]
    assert r["calls_carried"] == 2 and r["injected_tokens"] == 1_000


def test_v2_store_migrates_to_v3_by_forcing_a_full_reparse(tmp_path):
    import sqlite3
    import usage.store as store_mod
    from usage.parser import FileState
    db_path = tmp_path / "v2.db"
    raw = sqlite3.connect(str(db_path))
    schema_v2 = store_mod.SCHEMA.replace("sidechain INTEGER NOT NULL DEFAULT 0, agent_id TEXT NOT NULL DEFAULT '',\n  PRIMARY KEY(session_id, message_id)", "sidechain INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id, message_id)")
    assert "agent_id TEXT NOT NULL DEFAULT '',\n  PRIMARY KEY(session_id, message_id)" not in schema_v2
    raw.executescript(schema_v2)
    raw.execute("INSERT INTO sessions(session_id, first_ts, turns, tool_calls, repeated_reads) VALUES('A', ?, 7, 3, '{\"f\": 4}')", (iso(0),))
    row_sql = ("INSERT INTO calls(session_id,message_id,ts,day,model,input_tokens,cache_write,"
               "cache_read,output,thinking,sidechain) VALUES(?,?,?,?,?,0,0,0,0,0,?)")
    raw.execute(row_sql, ("A", "m1", iso(0), iso(0)[:10], "x", 0))
    raw.execute(row_sql, ("A", "x1", iso(1), iso(1)[:10], "x", 1))
    raw.execute("INSERT INTO files(path,size,mtime,offset,last_call_ts) VALUES('/a.jsonl', 10, 1.0, 10, NULL)")
    raw.execute("INSERT INTO costs_session(session_id,cost,models,fetched_at) VALUES('A', 1.5, '{}', 'now')")
    raw.execute("INSERT INTO meta(key,value) VALUES('schema_version','2')")
    raw.commit(); raw.close()

    s = Store(db_path)
    assert s.meta_get("schema_version") == store_mod.SCHEMA_VERSION == "4"
    cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(calls)")}
    assert "agent_id" in cols
    assert [tuple(r) for r in s.conn.execute("SELECT message_id, agent_id FROM calls")] == [("m1", "")]
    assert s.file_states() == {}                                   # every transcript is re-read next round
    sess = s.session("A")
    assert sess["turns"] == 0 and sess["tool_calls"] == 0 and sess["repeated_reads"] == {}
    assert sess["cost"] == 1.5                                     # costs are not parse-derived; kept
    # idempotent: opening again changes nothing
    s2 = Store(db_path)
    assert s2.meta_get("schema_version") == "4"
    s.save_file_states({"/b.jsonl": FileState("/b.jsonl", 1, 1.0, 1, None)})


def test_migration_adds_agent_id_to_a_skill_loads_table_created_without_it(tmp_path):
    import sqlite3
    db_path = tmp_path / "dev.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute("CREATE TABLE skill_loads(session_id TEXT NOT NULL, load_id TEXT NOT NULL, name TEXT NOT NULL, "
                "kind TEXT NOT NULL, ts TEXT, day TEXT, sidechain INTEGER NOT NULL DEFAULT 0, body_bytes INTEGER, "
                "PRIMARY KEY(session_id, load_id, name))")
    raw.commit(); raw.close()
    s = Store(db_path)
    assert "agent_id" in {r["name"] for r in s.conn.execute("PRAGMA table_info(skill_loads)")}
