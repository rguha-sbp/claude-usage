import json

from tests.helpers import (
    BASE_TS, ai_title, assistant_msg, compact_summary, iso, make_session,
    read_block, tool_result_msg, user_msg,
)


def test_iso_adds_minutes():
    assert iso(0) == BASE_TS
    assert iso(90) == "2026-09-01T10:30:00.000Z"


def test_records_have_expected_shape():
    u = user_msg("hello", iso(0))
    assert u["type"] == "user" and u["message"]["content"] == "hello"
    assert u["sessionId"] == "s1" and u["cwd"] == "/Users/x/proj"
    t = tool_result_msg("file contents", iso(1))
    assert t["message"]["content"][0]["type"] == "tool_result"
    c = compact_summary(iso(2))
    assert c["isCompactSummary"] is True and c["type"] == "user"
    a = assistant_msg("m1", iso(3), cache_write=100, cache_read=5000, output=20, thinking=5)
    usage = a["message"]["usage"]
    assert a["message"]["id"] == "m1"
    assert usage["cache_creation_input_tokens"] == 100
    assert usage["cache_read_input_tokens"] == 5000
    assert usage["output_tokens"] == 20
    assert usage["output_tokens_details"]["thinking_tokens"] == 5
    assert a["isSidechain"] is False
    r = read_block("/Users/x/proj/a.py")
    assert r["name"] == "Read" and r["input"]["file_path"] == "/Users/x/proj/a.py"
    ti = ai_title("My session")
    assert ti == {"type": "ai-title", "aiTitle": "My session", "sessionId": "s1"}


def test_make_session_layout(tmp_path):
    main = make_session(
        tmp_path, "-Users-x-proj", "s1",
        [user_msg("hi", iso(0)), assistant_msg("m1", iso(1))],
        subagents={"agent-a": [assistant_msg("m2", iso(2), sidechain=True)]},
    )
    assert main == tmp_path / "projects" / "-Users-x-proj" / "s1.jsonl"
    lines = main.read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(l) for l in lines)
    sub = tmp_path / "projects" / "-Users-x-proj" / "s1" / "subagents" / "agent-a.jsonl"
    assert sub.exists()
    assert json.loads(sub.read_text().splitlines()[0])["isSidechain"] is True
