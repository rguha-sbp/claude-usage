from tests.helpers import (
    ai_title, assistant_msg, compact_summary, iso, read_block, tool_result_msg, user_msg,
)
from usage.parser import Call, SessionAgg, derive_project, parse_records, parse_ts


def test_parse_ts_accepts_z_suffix():
    dt = parse_ts("2026-09-01T09:00:00.000Z")
    assert dt.year == 2026 and dt.hour == 9 and dt.tzinfo is not None


def test_derive_project_plain_and_worktree():
    assert derive_project("/Users/x/Code/repo") == "repo"
    assert derive_project("/Users/x/Code/repo/.claude/worktrees/feature-a") == "repo"
    assert derive_project("/") == "/"


def test_title_fallbacks():
    a = parse_records([user_msg("x" * 100, iso(0))], "s1")
    assert a.title is None and a.first_prompt == "x" * 80 and a.display_title() == "x" * 80
    b = parse_records([ai_title("Nice title"), user_msg("hi", iso(0))], "s1")
    assert b.display_title() == "Nice title"
    c = parse_records([], "s9")
    assert c.display_title() == "s9"


def test_cwd_project_and_timestamps():
    a = parse_records([
        user_msg("hi", iso(0), cwd="/Users/x/Code/repo/.claude/worktrees/wt"),
        assistant_msg("m1", iso(5), cwd="/Users/x/Code/repo/.claude/worktrees/wt"),
    ], "s1")
    assert a.cwd == "/Users/x/Code/repo/.claude/worktrees/wt"
    assert a.project == "repo"
    assert a.first_ts == iso(0) and a.last_ts == iso(5)


def test_turns_tool_results_and_compactions():
    a = parse_records([
        user_msg("hi", iso(0)),
        assistant_msg("m1", iso(1)),
        tool_result_msg("A" * 1000, iso(2)),
        assistant_msg("m2", iso(3)),
        compact_summary(iso(4)),
        user_msg("more", iso(5)),
    ], "s1")
    assert a.turns == 3          # "hi", compact summary, "more" (summary is a user turn)
    assert a.tool_calls == 1
    assert a.compactions == 1
    assert a.tool_result_bytes >= 1000
    assert a.content_bytes > a.tool_result_bytes


def test_message_id_merge_keeps_maximum_per_field():
    a = parse_records([
        assistant_msg("m1", iso(0), cache_write=100, cache_read=1000, output=5),
        assistant_msg("m1", iso(0), cache_write=100, cache_read=1000, output=120, thinking=30),
        assistant_msg("m2", iso(1), cache_read=2000, output=10),
    ], "s1")
    assert len(a.calls) == 2
    m1 = a.calls[0]
    assert m1.output == 120 and m1.thinking == 30 and m1.cache_read == 1000
    assert m1.context == 2 + 100 + 1000
    assert a.calls[1].message_id == "m2"


def test_cold_restarts_and_rebuild_tokens():
    a = parse_records([
        assistant_msg("m1", iso(0), cache_write=50),
        assistant_msg("m2", iso(30), cache_write=10),
        assistant_msg("m3", iso(120), cache_write=9000),   # 90 min gap -> cold
        assistant_msg("m4", iso(125), cache_write=10),
    ], "s1")
    assert a.cold_restarts == 1
    assert a.rebuild_tokens == 9000
    assert a.last_call_ts == iso(125)


def test_cold_restart_uses_prev_last_call_ts_across_chunks():
    a = parse_records([assistant_msg("m9", iso(300), cache_write=700)], "s1",
                      prev_last_call_ts=iso(0))
    assert a.cold_restarts == 1 and a.rebuild_tokens == 700


def test_read_counts_from_tool_use_blocks():
    recs = []
    for i in range(3):
        recs.append(assistant_msg("m%d" % i, iso(i), content=[read_block("/p/a.py", "t%d" % i)]))
    recs.append(assistant_msg("m9", iso(9), content=[read_block("/p/b.py", "t9")]))
    a = parse_records(recs, "s1")
    assert a.read_counts == {"/p/a.py": 3, "/p/b.py": 1}


def test_sidechain_marks_calls_and_counts_subagent_tokens():
    a = parse_records([assistant_msg("m1", iso(0), cache_read=500, output=20, sidechain=True)],
                      "s1", sidechain=True)
    assert a.calls[0].sidechain is True
    assert a.subagent_tokens == 2 + 500 + 20
    assert a.cold_restarts == 0


def test_merge_from_is_additive_and_keeps_title():
    a = parse_records([ai_title("T"), user_msg("hi", iso(0)), assistant_msg("m1", iso(1))], "s1")
    b = parse_records([assistant_msg("m2", iso(2), sidechain=True)], "s1", sidechain=True)
    a.merge_from(b)
    assert a.title == "T" and len(a.calls) == 2 and a.subagent_tokens == b.subagent_tokens
    assert a.last_ts == iso(2)


def test_unknown_record_types_are_ignored():
    a = parse_records([{"type": "queue-operation"}, {"type": "attachment", "x": 1}], "s1")
    assert isinstance(a, SessionAgg) and a.calls == [] and a.turns == 0


def test_malformed_message_usage_does_not_crash():
    """Malformed nested fields (non-dict usage, message) should be skipped, not crash."""
    # message.usage is a string instead of dict
    a = parse_records([
        {"type": "assistant", "message": {"id": "m1", "usage": "oops"}},
        assistant_msg("m2", iso(1))
    ], "s1")
    assert len(a.calls) == 1 and a.calls[0].message_id == "m2"


def test_malformed_message_field_does_not_crash():
    """Malformed message (string instead of dict) should be skipped, not crash."""
    a = parse_records([
        {"type": "assistant", "message": "not a dict"},
        assistant_msg("m3", iso(1))
    ], "s1")
    assert len(a.calls) == 1 and a.calls[0].message_id == "m3"


def test_malformed_user_message_field_does_not_crash():
    """Malformed user message (string instead of dict) should be skipped, no crash."""
    a = parse_records([
        {"type": "user", "message": "oops", "timestamp": iso(0)},
        user_msg("hello", iso(1))
    ], "s1")
    # Should have one turn (from valid user_msg), not crash
    assert a.turns == 1


def test_duplicate_message_ids_max_content_bytes_and_union_reads():
    """Duplicate message_ids should max content_bytes and union read paths, not sum."""
    small_content = [{"type": "text", "text": "x"}, read_block("/p/a.py", "t1")]
    large_content = [{"type": "text", "text": "y" * 1000}, read_block("/p/a.py", "t1")]
    a = parse_records([
        assistant_msg("m1", iso(0), content=small_content),
        assistant_msg("m1", iso(0), content=large_content)
    ], "s1")
    # Should have only one call (merged)
    assert len(a.calls) == 1
    # content_bytes should be the max of the two, not their sum
    # Verify it's the larger one by checking it's closer to large_content size
    expected_max = len(__import__("json").dumps(large_content).encode("utf-8"))
    assert a.content_bytes == expected_max
    # read_counts should have /p/a.py counted once, not twice
    assert a.read_counts == {"/p/a.py": 1}


def test_sidechain_call_does_not_update_last_call_ts():
    """Sidechain calls should not update last_call_ts (main thread tracking only)."""
    a = parse_records([
        assistant_msg("m1", iso(0)),
        assistant_msg("m2", iso(100), sidechain=True),  # Latest timestamp but sidechain
    ], "s1")
    # last_call_ts should be from m1 (iso(0)), not m2 (iso(100))
    assert a.last_call_ts == iso(0)


def test_malformed_tool_use_input_field_does_not_crash():
    """Malformed Read tool_use input (string instead of dict) should be skipped, no crash."""
    a = parse_records([{
        "type": "assistant",
        "message": {
            "id": "m1",
            "model": "x",
            "content": [{"type": "tool_use", "name": "Read", "input": "oops"}]
        },
        "timestamp": iso(0)
    }], "s1")
    # Should have one call but no read_counts entry, not crash
    assert len(a.calls) == 1
    assert a.read_counts == {}


# --- Fix Round 4: leaf-value hardening -------------------------------------

def test_read_file_path_leaf_type_does_not_crash_or_count():
    """file_path must be counted only when a non-empty str; list/int values are ignored."""
    a = parse_records([
        {"type": "assistant", "message": {"id": "m1", "model": "x",
            "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": ["a", "b"]}}]},
         "timestamp": iso(0)},
        {"type": "assistant", "message": {"id": "m2", "model": "x",
            "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": 12345}}]},
         "timestamp": iso(1)},
    ], "s1")
    assert a.read_counts == {}
    assert len(a.calls) == 2


def test_usage_leaf_values_coerce_safely():
    """Non-numeric strings and bools in usage fields coerce to 0; numeric strings coerce to int."""
    a = parse_records([{
        "type": "assistant",
        "message": {
            "id": "m1", "model": "x",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": "oops",
                "output_tokens": True,
                "cache_read_input_tokens": "42",
            },
        },
        "timestamp": iso(0),
    }], "s1")
    assert len(a.calls) == 1
    call = a.calls[0]
    assert call.input_tokens == 0
    assert call.output == 0
    assert call.cache_read == 42


def test_usage_leaf_non_finite_numbers_coerce_to_zero():
    """NaN/Infinity (as float or as the strings json emits) never raise; coerce to 0."""
    non_finite = [float("nan"), float("inf"), -float("inf"),
                  "inf", "-inf", "Infinity", "NaN"]
    for i, bad in enumerate(non_finite):
        for field_name in ("input_tokens", "cache_read_input_tokens", "output_tokens"):
            usage = {field_name: bad, "output_tokens_details": {"thinking_tokens": bad}}
            mid = "nf-%d-%s" % (i, field_name)
            a = parse_records([{
                "type": "assistant",
                "message": {"id": mid, "model": "x",
                    "content": [{"type": "text", "text": "ok"}], "usage": usage},
                "timestamp": iso(0),
            }], "s1")
            assert len(a.calls) == 1, "variant %r/%s crashed or was skipped" % (bad, field_name)
            call = a.calls[0]
            # Only one usage field is set to the non-finite variant per iteration;
            # every field must coerce to 0, whether set to `bad` or left absent.
            assert call.input_tokens == 0
            assert call.cache_read == 0
            assert call.output == 0
            assert call.thinking == 0


def test_message_id_and_uuid_non_str_skips_record():
    """A record whose message.id and uuid are both not non-empty strings is skipped."""
    a = parse_records([
        {"type": "assistant", "message": {"id": {"bad": True}, "model": "x",
            "content": [{"type": "text", "text": "ok"}], "usage": {}},
         "uuid": ["also", "bad"], "timestamp": iso(0)},
        assistant_msg("m2", iso(1)),
    ], "s1")
    assert len(a.calls) == 1
    assert a.calls[0].message_id == "m2"


def test_timestamp_leaf_type_does_not_crash():
    """A non-str timestamp on an assistant record is treated as missing, no exception."""
    a = parse_records([
        {"type": "assistant", "message": {"id": "m1", "model": "x",
            "content": [{"type": "text", "text": "ok"}], "usage": {}},
         "timestamp": 12345},
        assistant_msg("m2", iso(1)),
    ], "s1")
    assert len(a.calls) == 2
    assert a.calls[0].ts == ""
    # cold-restart logic must not break: m1 has no ts, so no gap computed against it
    assert a.cold_restarts == 0


_FUZZ_VARIANTS = [None, "", "x", 0, 1, True, [], {}, [1], {"a": 1},
                  float("nan"), float("inf"), "Infinity"]


def _fuzz_records(v):
    """~30 records, each with a single leaf field replaced by the fuzz variant v."""
    return [
        {"type": "user", "message": {"role": "user", "content": v}, "timestamp": iso(0), "cwd": "/x"},
        {"type": "user", "message": v, "timestamp": iso(0)},
        {"type": "user", "message": {"role": "user", "content": "hi"}, "timestamp": v},
        {"type": "user", "message": {"role": "user", "content": "hi"}, "timestamp": iso(0), "cwd": v},
        {"type": "user", "message": {"role": "user", "content": "hi"}, "timestamp": iso(0),
         "isCompactSummary": v},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": v}]},
         "timestamp": iso(0)},
        {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t", "content": v}]},
         "timestamp": iso(0)},
        {"type": "assistant", "message": {"id": v, "model": "m",
            "content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 2}},
         "uuid": "u1", "timestamp": iso(1)},
        {"type": "assistant", "message": v, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a1", "model": v,
            "content": [{"type": "text", "text": "ok"}], "usage": {}}, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a2", "model": "m", "content": v, "usage": {}},
         "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a3", "model": "m",
            "content": [{"type": "text", "text": v}], "usage": {}}, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a4", "model": "m",
            "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": v}}],
            "usage": {}}, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a5", "model": "m",
            "content": [{"type": "tool_use", "name": "Read", "input": v}], "usage": {}},
         "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a6", "model": "m", "content": [v], "usage": {}},
         "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a7", "model": "m",
            "content": [{"type": "text", "text": "ok"}], "usage": v}, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a8", "model": "m",
            "content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": v}},
         "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a9", "model": "m",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"cache_creation_input_tokens": v}}, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a10", "model": "m",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"cache_read_input_tokens": v}}, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a11", "model": "m",
            "content": [{"type": "text", "text": "ok"}], "usage": {"output_tokens": v}},
         "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a12", "model": "m",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"output_tokens_details": v}}, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a13", "model": "m",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"output_tokens_details": {"thinking_tokens": v}}}, "timestamp": iso(1)},
        {"type": "assistant", "message": {"id": "a14", "model": "m",
            "content": [{"type": "text", "text": "ok"}], "usage": {}},
         "timestamp": iso(1), "isSidechain": v},
        {"type": "assistant", "message": {"id": "a15", "model": "m",
            "content": [{"type": "text", "text": "ok"}], "usage": {}}, "timestamp": v},
        {"type": "assistant", "message": {"id": "a16", "model": "m",
            "content": [{"type": "text", "text": "ok"}], "usage": {}},
         "timestamp": iso(1), "cwd": v},
        {"type": "assistant", "uuid": v, "message": {"model": "m",
            "content": [{"type": "text", "text": "ok"}], "usage": {}}, "timestamp": iso(1)},
        {"type": "ai-title", "aiTitle": v, "sessionId": "s1"},
        {"type": v},
        v,
    ]


def _assert_variant_does_not_crash(v):
    try:
        result = parse_records(_fuzz_records(v), "s1")
    except Exception as exc:  # noqa: BLE001 - intentionally broad for fuzz robustness
        raise AssertionError("parse_records crashed for variant %r: %r" % (v, exc))
    assert isinstance(result, SessionAgg)


def test_fuzz_every_leaf_field_never_crashes():
    for variant in _FUZZ_VARIANTS:
        _assert_variant_does_not_crash(variant)
