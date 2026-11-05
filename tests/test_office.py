"""The Office tab's data (spec Sec 12.2): live sessions from the statusline's
state files, classified working / asleep / gone, grouped into rooms by project."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.helpers import assistant_msg, iso, user_msg, write_jsonl
from usage.office import Office, tail_activity

NOW = datetime(2026, 9, 15, 8, 0, 0, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")   # a live agent's last record


def _state(state_dir: Path, sid: str, seen_ago: int, cwd="/Users/x/Code/proj", pid=100, **extra) -> None:
    state = {"session_id": sid, "title": "Session " + sid, "cwd": cwd, "model": "Fable 5.1",
             "seen": (NOW - timedelta(seconds=seen_ago)).isoformat(timespec="seconds")}
    if pid is not None:
        state["pid"] = pid
    state.update(extra)
    (state_dir / (sid + ".json")).write_text(json.dumps(state))


def _office(tmp_path, alive_pids=(100,), **kw) -> Office:
    return Office(tmp_path, claude_dir=tmp_path, alive=lambda pid: pid in alive_pids, **kw)


def _workers(payload):
    return {w["session_id"]: w for room in payload["rooms"] for w in room["workers"]}


def test_working_asleep_and_gone(tmp_path):
    _state(tmp_path, "busy", seen_ago=10)
    _state(tmp_path, "quiet", seen_ago=600)
    _state(tmp_path, "dead", seen_ago=10, pid=999)                # process no longer exists
    _state(tmp_path, "nopid-fresh", seen_ago=10, pid=None)
    _state(tmp_path, "nopid-old", seen_ago=90000, pid=None)        # older than gone_after
    payload = _office(tmp_path).payload(now=NOW)
    w = _workers(payload)
    assert set(w) == {"busy", "quiet", "nopid-fresh"}
    assert w["busy"]["state"] == "working" and w["quiet"]["state"] == "asleep"
    assert w["nopid-fresh"]["state"] == "working"
    assert payload["working_seconds"] == 60 and payload["generated_at"]


def test_rooms_group_by_project_and_order_by_size_then_name(tmp_path):
    _state(tmp_path, "a", 5, cwd="/Users/x/Code/zeta")
    _state(tmp_path, "b", 5, cwd="/Users/x/Code/alpha")
    _state(tmp_path, "c", 5, cwd="/Users/x/Code/alpha/.claude/worktrees/feature-1")   # worktree -> alpha
    _state(tmp_path, "d", 5, cwd="/Users/x/Code/beta")
    payload = _office(tmp_path).payload(now=NOW)
    assert [(r["project"], len(r["workers"])) for r in payload["rooms"]] == [("alpha", 2), ("beta", 1), ("zeta", 1)]
    alpha = payload["rooms"][0]
    assert alpha["cwd"] == "/Users/x/Code/alpha"
    assert [w["title"] for w in alpha["workers"]] == ["Session b", "Session c"]


def test_since_tracks_the_working_streak_and_the_moment_a_sleeper_fell_quiet(tmp_path):
    office = _office(tmp_path)
    _state(tmp_path, "s", seen_ago=50)
    first = _workers(office.payload(now=NOW))["s"]
    assert first["state"] == "working" and first["since"] == (NOW - timedelta(seconds=50)).isoformat(timespec="seconds")
    _state(tmp_path, "s", seen_ago=5)                               # still working, seen again
    again = _workers(office.payload(now=NOW))["s"]
    assert again["since"] == first["since"]                         # streak start is kept
    later = NOW + timedelta(seconds=300)
    asleep = _workers(office.payload(now=later))["s"]
    assert asleep["state"] == "asleep" and asleep["since"] == (NOW - timedelta(seconds=5)).isoformat(timespec="seconds")
    woke = later + timedelta(seconds=10)
    _state(tmp_path, "s", seen_ago=-310)                            # seen at `woke`
    back = _workers(office.payload(now=woke))["s"]
    assert back["state"] == "working" and back["since"] == woke.isoformat(timespec="seconds")


def test_sleepers_carry_the_last_assistant_message_from_their_transcript(tmp_path):
    transcript = write_jsonl(tmp_path / "projects" / "-x" / "t.jsonl", [
        user_msg("hello", iso(0)),
        assistant_msg("m1", iso(1)),
        {"type": "assistant", "message": {"id": "m2", "role": "assistant", "content": [
            {"type": "text", "text": "Should I delete the old table?"}]}, "timestamp": iso(2)},
        user_msg("later", iso(3)),
    ])
    _state(tmp_path, "sleepy", seen_ago=900, transcript_path=str(transcript))
    _state(tmp_path, "busy", seen_ago=1, transcript_path=str(transcript))
    _state(tmp_path, "lost", seen_ago=900, transcript_path=str(tmp_path / "missing.jsonl"))
    _state(tmp_path, "escaped", seen_ago=900, transcript_path="/etc/passwd")
    w = _workers(_office(tmp_path).payload(now=NOW))
    assert w["sleepy"]["last_message"] == "Should I delete the old table?"
    assert w["busy"]["last_message"] is None            # only sleepers pay for the read
    assert w["lost"]["last_message"] is None and w["escaped"]["last_message"] is None


def test_tail_activity_reads_only_the_tail_and_truncates(tmp_path):
    big = "x" * 1000
    records = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "old %d" % i}]}} for i in range(500)]
    records.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                                 "input": {"command": "pytest -q"}},
                                                                {"type": "text", "text": big}]}})
    path = write_jsonl(tmp_path / "big.jsonl", records)
    act = tail_activity(path, tmp_path)
    assert act["last_message"] is not None and len(act["last_message"]) == 400 and act["last_message"].startswith("xxx")
    assert act["doing"] == {"tool": "Bash", "target": "pytest -q"}


def test_tail_activity_skips_thinking_for_doing_and_is_empty_when_missing(tmp_path):
    path = write_jsonl(tmp_path / "t.jsonl", [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/a.py"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hm"}]}},
    ])
    assert tail_activity(path, tmp_path) == {"last_message": None, "doing": {"tool": "Edit", "target": "/a.py"}}
    assert tail_activity(tmp_path / "missing.jsonl", tmp_path) == {"last_message": None, "doing": None}


def test_workers_carry_their_doing_and_their_sub_agents(tmp_path):
    transcript = write_jsonl(tmp_path / "projects" / "-x" / "t.jsonl", [
        assistant_msg("m1", iso(0), content=[{"type": "tool_use", "id": "toolu_a1", "name": "Agent",
                                             "input": {"description": "look around"}}])])
    folder = transcript.with_suffix("") / "subagents"
    folder.mkdir(parents=True)
    (folder / "agent-a1.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "look around",
                                                           "toolUseId": "toolu_a1", "spawnDepth": 1}))
    write_jsonl(folder / "agent-a1.jsonl", [assistant_msg("s1", iso(1), content=[
        {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "office"}}])])
    _state(tmp_path, "busy", seen_ago=1, transcript_path=str(transcript))
    _state(tmp_path, "plain", seen_ago=1)
    w = _workers(_office(tmp_path).payload(now=NOW))
    assert w["busy"]["doing"] == {"tool": "Agent", "target": "look around"}
    assert w["busy"]["agents_hidden"] == 0
    [sub] = w["busy"]["agents"]
    assert sub["agent_id"] == "a1" and sub["type"] == "Explore" and sub["doing"] == {"tool": "Grep", "target": "office"}
    assert w["plain"]["agents"] == [] and w["plain"]["doing"] is None and w["plain"]["agents_hidden"] == 0


def _transcript_with_running_agent(tmp_path, sid, agent_last):
    transcript = write_jsonl(tmp_path / "projects" / "-x" / (sid + ".jsonl"), [
        assistant_msg("m1", iso(0), content=[{"type": "tool_use", "id": "toolu_a1", "name": "Agent",
                                             "input": {"description": "look around"}}])])
    folder = transcript.with_suffix("") / "subagents"
    folder.mkdir(parents=True)
    (folder / "agent-a1.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "x",
                                                           "toolUseId": "toolu_a1", "spawnDepth": 1}))
    write_jsonl(folder / "agent-a1.jsonl", [assistant_msg("s1", agent_last, content=[
        {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "office"}}])])
    return transcript


def test_doing_is_sent_only_while_something_is_live(tmp_path):
    transcript = _transcript_with_running_agent(tmp_path, "t", RECENT)
    _state(tmp_path, "asleep-idle", seen_ago=900, transcript_path=str(write_jsonl(
        tmp_path / "projects" / "-x" / "idle.jsonl", [assistant_msg("m1", iso(0), content=[
            {"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": "ls"}}])])))
    _state(tmp_path, "asleep-busy", seen_ago=900, transcript_path=str(transcript))
    w = _workers(_office(tmp_path).payload(now=NOW))
    assert w["asleep-idle"]["state"] == "asleep" and w["asleep-idle"]["doing"] is None
    assert w["asleep-busy"]["doing"] == {"tool": "Agent", "target": "look around"}   # an intern is running


def test_payload_forgets_the_agents_of_sessions_that_left(tmp_path):
    a = _transcript_with_running_agent(tmp_path, "a", RECENT)
    b = _transcript_with_running_agent(tmp_path, "b", RECENT)
    _state(tmp_path, "a", seen_ago=1, transcript_path=str(a))
    _state(tmp_path, "b", seen_ago=1, transcript_path=str(b))
    office = _office(tmp_path)
    office.payload(now=NOW)
    assert len(office.reader._tallies) == 2
    (tmp_path / "b.json").unlink()
    office.payload(now=NOW)
    assert [p.parent.parent for p in office.reader._tallies] == [a.resolve().with_suffix("")]


def test_unusable_state_files_are_skipped(tmp_path):
    (tmp_path / "garbage.json").write_text("{not json")
    (tmp_path / "old-shape.json").write_text(json.dumps({"announced": "none", "title": "pre-office file"}))
    _state(tmp_path, "ok", 1)
    payload = _office(tmp_path).payload(now=NOW)
    assert list(_workers(payload)) == ["ok"]


# -- notice board -----------------------------------------------------------
# The plaza's board shows what today actually cost and which project is
# busiest; the server attaches it to /api/office (spec Sec 12.7).

def _store_with_a_day(tmp_path):
    from usage.parser import parse_records
    from usage.store import Store, local_day
    store = Store(tmp_path / "board.db")
    store.upsert_chunk(parse_records([user_msg("hi", iso(0), cwd="/Users/x/Code/alpha"),
                                      assistant_msg("m1", iso(1), output=100)], "s1"))
    store.upsert_chunk(parse_records([user_msg("hi", iso(0), session_id="s2", cwd="/Users/x/Code/beta"),
                                      assistant_msg("m2", iso(1), session_id="s2", output=10)], "s2"))
    store.recompute(threshold=1000)
    return store, local_day(iso(1))


def test_board_stats_reports_the_days_spend_and_the_busiest_project(tmp_path):
    from usage.summary import board_stats
    store, day = _store_with_a_day(tmp_path)
    store.set_daily_costs([(day, "*", 2.0), (day, "claude-opus-5", 1.5), (day, "claude-sonnet-5", 0.5),
                           ("2000-01-01", "*", 9.0)])
    store.set_allocated_costs([(day, "alpha", "s1", 1.6), (day, "beta", "s2", 0.4)])
    board = board_stats(store, day, cost_available=True)
    assert board["day"] == day and board["unit"] == "usd"
    assert board["value"] == pytest.approx(2.0)                 # only this day
    assert board["top_project"] == "alpha" and board["top_value"] == pytest.approx(1.6)
    assert board["tokens"] > 0


def test_board_stats_falls_back_to_tokens_without_ccusage(tmp_path):
    store, day = _store_with_a_day(tmp_path)
    board = board_stats_of(store, day)
    assert board["unit"] == "tokens" and board["value"] == board["tokens"]
    assert board["top_project"] == "alpha"                      # the heavier session's project


def board_stats_of(store, day):
    from usage.summary import board_stats
    return board_stats(store, day, cost_available=False)


def test_board_stats_on_an_empty_day(tmp_path):
    from usage.store import Store
    store = Store(tmp_path / "empty.db")
    board = board_stats_of(store, "2026-09-16")
    assert board["value"] == 0 and board["top_project"] is None and board["tokens"] == 0
