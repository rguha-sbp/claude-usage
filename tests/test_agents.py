"""Sub-agents of a live session (spec Sec 13.2): read from the subagents/
folder beside the session's transcript, incrementally, with no database."""
import json
from dataclasses import fields
from datetime import datetime, timedelta, timezone

from tests.helpers import assistant_msg, iso, tool_result_msg, user_msg, write_jsonl
from usage.agents import AgentReader, SubAgent, doing_of
from usage.parser import parse_records

NOW = datetime(2026, 9, 1, 9, 5, 0, tzinfo=timezone.utc)      # iso(5)


def _session(tmp_path, sid="s1"):
    transcript = write_jsonl(tmp_path / "projects" / "-x" / (sid + ".jsonl"), [user_msg("go", iso(0))])
    return transcript, transcript.with_suffix("") / "subagents"


def _agent(folder, aid, records, **meta):
    m = {"agentType": "Explore", "description": "find things", "toolUseId": "toolu_" + aid, "spawnDepth": 1}
    m.update(meta)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("agent-%s.meta.json" % aid)).write_text(json.dumps(m))
    return write_jsonl(folder / ("agent-%s.jsonl" % aid), records)


def tool(name, tid="t1", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _read(tmp_path, transcript, now=NOW):
    return AgentReader(tmp_path).session_agents(transcript, now)


def _by_id(agents):
    return {a.agent_id: a for a in agents}


def test_meta_fields_and_a_running_agent(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "a1", [user_msg("look", iso(0)),
                          assistant_msg("m1", iso(4), model="claude-sonnet-5",
                                        content=[tool("Grep", pattern="subagent")])])
    agents, hidden = _read(tmp_path, transcript)
    a = _by_id(agents)["a1"]
    assert hidden == 0
    assert (a.type, a.description, a.depth, a.parent, a.background) == ("Explore", "find things", 1, None, False)
    assert a.model == "claude-sonnet-5" and a.state == "running"
    assert a.doing == {"tool": "Grep", "target": "subagent"}
    assert a.tools == 1 and a.tool_mix == {"Grep": 1}
    assert a.started == iso(0) and a.last == iso(4)


def test_nested_agent_links_to_the_agent_that_spawned_it(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Agent", tid="toolu_a2", description="plan it")])])
    _agent(folder, "a2", [assistant_msg("m2", iso(2), content=[tool("Read", file_path="/x/y.py")])],
           agentType="Plan", spawnDepth=2, requestShape="background")
    agents = _by_id(_read(tmp_path, transcript)[0])
    assert agents["a2"].parent == "a1" and agents["a2"].depth == 2 and agents["a2"].background
    assert agents["a1"].parent is None and agents["a1"].doing == {"tool": "Agent", "target": "plan it"}


def test_tokens_merge_each_message_once_like_the_parser(tmp_path):
    transcript, folder = _session(tmp_path)
    records = [
        assistant_msg("m1", iso(1), input_tokens=100, cache_read=50, output=4, content=[{"type": "thinking", "thinking": "."}]),
        assistant_msg("m1", iso(1), input_tokens=100, cache_read=50, output=10, content=[tool("Read", file_path="/a")]),
        assistant_msg("m2", iso(2), input_tokens=5, output=5, content=[tool("Bash", tid="t2", command="ls")]),
    ]
    _agent(folder, "a1", records)
    a = _by_id(_read(tmp_path, transcript)[0])["a1"]
    expected = parse_records([dict(r, isSidechain=True) for r in records], "s1", sidechain=True,
                             agent_id="agent-a1").subagent_tokens
    assert a.tokens == expected == 170
    assert a.tools == 2 and a.tool_mix == {"Read": 1, "Bash": 1}


def test_handback_means_done_and_is_not_counted_as_work(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Read", file_path="/a")]),
                          assistant_msg("m2", iso(4), content=[tool("SubagentHandback", tid="t2")])])
    a = _by_id(_read(tmp_path, transcript)[0])["a1"]
    assert a.state == "done" and a.tools == 1 and a.tool_mix == {"Read": 1}


def test_a_legacy_text_reply_is_done_once_quiet(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "a1", [assistant_msg("m1", iso(4), content=[{"type": "text", "text": "Found it."}])])
    assert _by_id(_read(tmp_path, transcript)[0])["a1"].state == "done"                   # 60 s quiet
    just_now = datetime(2026, 9, 1, 9, 4, 5, tzinfo=timezone.utc)
    reader = AgentReader(tmp_path)
    a = _by_id(reader.session_agents(transcript, just_now)[0])["a1"]
    assert a.state == "running" and a.doing == {"tool": "replying", "target": ""}


def test_a_pending_tool_call_is_work_for_up_to_fifteen_minutes(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "a1", [assistant_msg("m1", iso(0), content=[tool("Bash", command="sleep 9999")])])
    assert _by_id(_read(tmp_path, transcript)[0])["a1"].state == "running"                # a long Bash is still work
    later = NOW + timedelta(minutes=11)                                                  # 16 min after iso(0)
    assert _by_id(_read(tmp_path, transcript, later)[0])["a1"].state == "asleep"


def test_asleep_once_quiet_past_the_working_window(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Grep", pattern="p")]),
                          tool_result_msg("hit", iso(2))])
    assert _by_id(_read(tmp_path, transcript)[0])["a1"].state == "asleep"                # 3 min quiet, no tool pending
    soon = datetime(2026, 9, 1, 9, 2, 30, tzinfo=timezone.utc)
    assert _by_id(_read(tmp_path, transcript, soon)[0])["a1"].state == "running"         # 30 s quiet
    patient = AgentReader(tmp_path, working_seconds=600)
    assert _by_id(patient.session_agents(transcript, NOW)[0])["a1"].state == "running"


def test_thinking_only_keeps_the_last_doing_and_targets_are_trimmed(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Bash", command="x" * 200)]),
                          assistant_msg("m2", iso(2), content=[{"type": "thinking", "thinking": "hmm"}])])
    a = _by_id(_read(tmp_path, transcript)[0])["a1"]
    assert a.doing["tool"] == "Bash" and a.doing["target"] == "x" * 80


def test_incremental_reads_only_appended_bytes(tmp_path):
    transcript, folder = _session(tmp_path)
    path = _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Grep", pattern="p")])])
    reader = AgentReader(tmp_path)
    reader.session_agents(transcript, NOW)
    # rewrite the already-read line in place (same length) and append a new one:
    # only the appended line may change the totals
    old = path.read_text()
    path.write_text(old.replace('"Grep"', '"Glob"') + json.dumps(
        assistant_msg("m2", iso(2), content=[tool("Read", tid="t2", file_path="/a")])) + "\n")
    a = _by_id(reader.session_agents(transcript, NOW)[0])["a1"]
    assert a.tool_mix == {"Grep": 1, "Read": 1}


def test_a_shrunk_file_is_read_again_from_the_start(tmp_path):
    transcript, folder = _session(tmp_path)
    path = _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Grep", pattern="p")]),
                                 assistant_msg("m2", iso(2), content=[tool("Read", tid="t2", file_path="/a")])])
    reader = AgentReader(tmp_path)
    reader.session_agents(transcript, NOW)
    write_jsonl(path, [assistant_msg("m9", iso(3), content=[tool("Bash", command="ls")])])
    a = _by_id(reader.session_agents(transcript, NOW)[0])["a1"]
    assert a.tool_mix == {"Bash": 1} and a.started == iso(3)


def test_cut_last_line_waits_for_the_next_read(tmp_path):
    transcript, folder = _session(tmp_path)
    path = _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Grep", pattern="p")])])
    second = json.dumps(assistant_msg("m2", iso(2), content=[tool("Read", tid="t2", file_path="/a")]))
    with path.open("a") as fh:
        fh.write(second[:30])
    reader = AgentReader(tmp_path)
    assert _by_id(reader.session_agents(transcript, NOW)[0])["a1"].tools == 1
    with path.open("a") as fh:
        fh.write(second[30:] + "\n")
    assert _by_id(reader.session_agents(transcript, NOW)[0])["a1"].tool_mix == {"Grep": 1, "Read": 1}


def test_agents_without_meta_or_records_are_skipped(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "ok", [assistant_msg("m1", iso(1))])
    write_jsonl(folder / "agent-nometa.jsonl", [assistant_msg("m2", iso(1))])
    _agent(folder, "empty", [])
    (folder / "agent-badmeta.meta.json").write_text("{nope")
    write_jsonl(folder / "agent-badmeta.jsonl", [assistant_msg("m3", iso(1))])
    assert [a.agent_id for a in _read(tmp_path, transcript)[0]] == ["ok"]


def test_window_cap_and_hidden(tmp_path):
    transcript, folder = _session(tmp_path)
    handback = [tool("SubagentHandback")]
    for k in range(25):
        _agent(folder, "d%02d" % k, [assistant_msg("m%d" % k, iso(k), content=handback)])
    _agent(folder, "old", [assistant_msg("mo", iso(-200), content=handback)])           # over 2 h before `now`
    _agent(folder, "live", [assistant_msg("ml", iso(29), content=[tool("Read", file_path="/a")])])
    now = datetime(2026, 9, 1, 9, 30, 0, tzinfo=timezone.utc)
    agents, hidden = _read(tmp_path, transcript, now)
    ids = [a.agent_id for a in agents]
    assert hidden == 6                                   # five beyond the cap of 20, plus the old one
    assert ids == ["d%02d" % k for k in range(5, 25)] + ["live"]


def test_paths_outside_claude_dir_and_missing_folders_give_nothing(tmp_path):
    transcript, folder = _session(tmp_path)
    _agent(folder, "a1", [assistant_msg("m1", iso(1))])
    assert AgentReader(tmp_path / "elsewhere").session_agents(transcript, NOW) == ([], 0)
    lonely = write_jsonl(tmp_path / "projects" / "-x" / "s2.jsonl", [user_msg("hi", iso(0))])
    assert AgentReader(tmp_path).session_agents(lonely, NOW) == ([], 0)


def test_doing_of_shapes():
    assert doing_of(None) is None and doing_of([]) is None
    assert doing_of([{"type": "thinking", "thinking": "x"}]) is None
    assert doing_of("plain reply") == {"tool": "replying", "target": ""}
    assert doing_of([tool("WebFetch", url="https://a.b")]) == {"tool": "WebFetch", "target": "https://a.b"}
    assert doing_of([tool("mcp__x__y")]) == {"tool": "mcp__x__y", "target": ""}
    assert [f.name for f in fields(SubAgent)] == ["agent_id", "type", "description", "background", "depth", "parent",
                                                   "model", "tokens", "tools", "tool_mix", "doing", "started",
                                                   "last", "state"]
