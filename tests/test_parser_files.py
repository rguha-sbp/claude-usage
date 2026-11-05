import json
import os
import time

from tests.helpers import ai_title, assistant_msg, iso, make_session, user_msg
from usage.parser import FileState, discover, parse_file, parse_incremental


def test_discover_finds_sessions_and_subagents(tmp_path):
    make_session(tmp_path, "-Users-x-a", "s1", [user_msg("hi", iso(0))],
                 subagents={"agent-1": [assistant_msg("m1", iso(1), sidechain=True)]})
    make_session(tmp_path, "-Users-x-b", "s2", [user_msg("yo", iso(0))])
    (tmp_path / "projects" / "-Users-x-a" / "notes.txt").write_text("ignore me")
    found = sorted(discover(tmp_path), key=lambda t: str(t.path))
    ids = [(t.session_id, t.parent_session_id) for t in found]
    assert ("s1", None) in ids and ("s2", None) in ids
    assert ("agent-1", "s1") in ids
    assert len(found) == 3


def test_parse_file_consumes_only_complete_lines(tmp_path):
    p = make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0)), assistant_msg("m1", iso(1))])
    with p.open("a") as fh:
        fh.write('{"type":"assistant","message":{"id":"m2"')   # partial line, no newline
    full_len = p.stat().st_size
    res = parse_file(p, "s1")
    assert res.agg.turns == 1 and len(res.agg.calls) == 1
    assert res.new_offset < full_len
    assert res.warnings == []


def test_parse_file_from_offset_and_reports_bad_json(tmp_path):
    p = make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0))])
    first = parse_file(p, "s1")
    with p.open("a") as fh:
        fh.write("not json\n")
        fh.write(json.dumps(assistant_msg("m1", iso(5), cache_write=3)) + "\n")
    second = parse_file(p, "s1", offset=first.new_offset, prev_last_call_ts=None)
    assert second.agg.turns == 0 and len(second.agg.calls) == 1
    assert any("invalid JSON" in w for w in second.warnings)
    assert second.new_offset == p.stat().st_size


def test_parse_incremental_append_skip_and_shrink(tmp_path):
    p = make_session(tmp_path, "-p", "s1", [ai_title("T"), user_msg("hi", iso(0)),
                                            assistant_msg("m1", iso(1), cache_write=10)])
    r1 = parse_incremental(tmp_path, {})
    assert set(r1.chunks) == {"s1"}
    assert r1.chunks["s1"].title == "T" and len(r1.chunks["s1"].calls) == 1
    st = r1.file_states[str(p)]
    assert st.offset == p.stat().st_size and st.last_call_ts == iso(1)
    assert str(p) in r1.present_paths

    # unchanged -> skipped
    r2 = parse_incremental(tmp_path, r1.file_states)
    assert r2.chunks == {}

    # append 90 minutes later -> only new call, cold restart detected via stored last_call_ts
    time.sleep(0.01)
    with p.open("a") as fh:
        fh.write(json.dumps(assistant_msg("m2", iso(95), cache_write=400)) + "\n")
    os.utime(p, None)
    r3 = parse_incremental(tmp_path, r1.file_states)
    c = r3.chunks["s1"]
    assert [x.message_id for x in c.calls] == ["m2"]
    assert c.cold_restarts == 1 and c.rebuild_tokens == 400
    assert c.title is None   # title was in the already-consumed part

    # shrink -> reparse from zero
    p.write_text(json.dumps(user_msg("fresh", iso(0))) + "\n")
    r4 = parse_incremental(tmp_path, r3.file_states)
    assert r4.chunks["s1"].turns == 1 and r4.chunks["s1"].calls == []
    assert r4.file_states[str(p)].offset == p.stat().st_size


def test_discover_ignores_directory_named_jsonl(tmp_path):
    make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0))])
    (tmp_path / "projects" / "-p" / "x.jsonl").mkdir()
    found = discover(tmp_path)
    assert all(t.path.is_file() for t in found)
    assert len(found) == 1


def test_parse_incremental_skips_unreadable_file_and_continues(tmp_path):
    make_session(tmp_path, "-p", "good", [user_msg("hi", iso(0))])
    bad_path = make_session(tmp_path, "-p", "bad", [user_msg("hi", iso(0))])
    os.chmod(bad_path, 0o000)
    try:
        r = parse_incremental(tmp_path, {})
    finally:
        os.chmod(bad_path, 0o644)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return  # root bypasses permission bits; nothing to assert
    assert "good" in r.chunks
    assert "bad" not in r.chunks
    assert any(str(bad_path) in w for w in r.warnings)


def test_parse_file_caps_warnings_at_20_with_overflow_summary(tmp_path):
    p = tmp_path / "s1.jsonl"
    with p.open("w") as fh:
        for i in range(25):
            fh.write("not json %d\n" % i)
    res = parse_file(p, "s1")
    assert len(res.warnings) == 21
    assert res.warnings[-1] == "…5 more"
    assert res.new_offset == p.stat().st_size


def test_parse_file_skips_non_dict_json_line(tmp_path):
    p = tmp_path / "s1.jsonl"
    with p.open("w") as fh:
        fh.write(json.dumps([1, 2, 3]) + "\n")
        fh.write(json.dumps(assistant_msg("m1", iso(0))) + "\n")
    res = parse_file(p, "s1")
    assert len(res.agg.calls) == 1
    assert any("invalid JSON" in w for w in res.warnings)


def test_parse_incremental_attributes_subagents_to_parent(tmp_path):
    make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0)), assistant_msg("m1", iso(1), cache_read=100)],
                 subagents={"a": [assistant_msg("x1", iso(2), cache_read=1000, output=50, sidechain=True)]})
    r = parse_incremental(tmp_path, {})
    assert set(r.chunks) == {"s1"}
    agg = r.chunks["s1"]
    assert len(agg.calls) == 2
    assert agg.subagent_tokens == 2 + 1000 + 50
    assert sum(1 for c in agg.calls if c.sidechain) == 1
    assert len(r.file_states) == 2


def test_parse_incremental_stamps_subagent_transcripts_with_their_file_name(tmp_path):
    from tests.helpers import make_session
    from usage.parser import parse_incremental
    make_session(tmp_path, "-proj", "s1",
                 [user_msg("hi", iso(0)), assistant_msg("m1", iso(1))],
                 subagents={"agent-abc": [assistant_msg("x1", iso(2), sidechain=True)]})
    res = parse_incremental(tmp_path, {})
    agents = {c.message_id: c.agent_id for c in res.chunks["s1"].calls}
    assert agents == {"m1": "", "x1": "agent-abc"}
