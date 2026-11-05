"""Sub-agents of a live session (spec Sec 13.2): read from the subagents/
folder beside the session's transcript, incrementally, with no database."""
import json
from dataclasses import fields
from datetime import datetime, timedelta, timezone

from tests.helpers import assistant_msg, iso, tool_result_msg, user_msg, write_jsonl
from usage import agents as agents_module
from usage.agents import AgentReader, SubAgent, doing_of
from usage.parser import parse_records

NOW = datetime(2026, 9, 1, 9, 5, 0, tzinfo=timezone.utc)      # iso(5)
INTERRUPTED = "[Request interrupted by user]"                 # how Claude Code closes a turn it stopped


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
                          tool_result_msg("hit", iso(2)), user_msg(INTERRUPTED, iso(2))])
    assert _by_id(_read(tmp_path, transcript)[0])["a1"].state == "asleep"                # 3 min quiet, turn closed
    soon = datetime(2026, 9, 1, 9, 2, 30, tzinfo=timezone.utc)
    assert _by_id(_read(tmp_path, transcript, soon)[0])["a1"].state == "running"         # 30 s quiet
    patient = AgentReader(tmp_path, working_seconds=600)
    assert _by_id(patient.session_agents(transcript, NOW)[0])["a1"].state == "running"


def _append(path, *records):
    with path.open("a") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_parallel_tool_calls_close_one_by_one(tmp_path):
    """Three agents spawned in one turn: the first two results must not
    clear the call that still waits on the third."""
    calls = [tool("Agent", tid="toolu_a%d" % k, description="d") for k in (1, 2, 3)]
    transcript = write_jsonl(tmp_path / "projects" / "-x" / "s1.jsonl", [
        user_msg("go", iso(0)), assistant_msg("lead", iso(0), content=calls, stop_reason="tool_use"),
        tool_result_msg("a", iso(2), tool_use_id="toolu_a1"), tool_result_msg("b", iso(2), tool_use_id="toolu_a2")])
    folder = transcript.with_suffix("") / "subagents"
    for k in (1, 2, 3):
        _agent(folder, "a%d" % k, [assistant_msg("m%d" % k, iso(1), stop_reason="end_turn",
                                                 content=[{"type": "text", "text": "Found it."}])])
    reader = AgentReader(tmp_path)
    states = {a.agent_id: a.state for a in reader.session_agents(transcript, NOW)[0]}
    assert states == {"a1": "done", "a2": "done", "a3": "running"}                       # the lead still waits on a3
    _append(transcript, tool_result_msg("c", iso(3), tool_use_id="toolu_a3"))
    assert _by_id(reader.session_agents(transcript, NOW)[0])["a3"].state == "done"


def test_an_open_turn_is_work_for_up_to_fifteen_minutes(tmp_path):
    """No record while the model composes its next block: after a tool
    result, or after a block of a message that has more to come (no
    `stop_reason` yet), the agent is still at work."""
    transcript, folder = _session(tmp_path)
    answered = _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Grep", pattern="p")], stop_reason="tool_use"),
                                     tool_result_msg("hit", iso(1))])
    _agent(folder, "a2", [assistant_msg("m2", iso(1), content=[{"type": "thinking", "thinking": "."}])])
    _agent(folder, "a3", [assistant_msg("m3", iso(1), content=[tool("Grep", pattern="p")], stop_reason="tool_use"),
                          tool_result_msg("hit", iso(1)), user_msg(INTERRUPTED, iso(1))])
    reader = AgentReader(tmp_path)
    states = {a.agent_id: a.state for a in reader.session_agents(transcript, NOW)[0]}
    assert states == {"a1": "running", "a2": "running", "a3": "asleep"}                  # 4 min quiet
    later = NOW + timedelta(minutes=12)                                                  # 16 min after iso(1)
    assert {a.state for a in reader.session_agents(transcript, later)[0]} == {"asleep"}
    _append(answered, user_msg(INTERRUPTED, iso(2)))
    assert _by_id(reader.session_agents(transcript, NOW)[0])["a1"].state == "asleep"     # the turn was closed


def test_long_transcripts_are_read_in_bounded_steps(tmp_path, monkeypatch):
    """A step smaller than one record still folds every record once, the
    lead's open calls included."""
    monkeypatch.setattr(agents_module, "READ_BYTES", 7)
    transcript = write_jsonl(tmp_path / "projects" / "-x" / "s1.jsonl", [
        user_msg("go", iso(0)), assistant_msg("lead", iso(0), content=[tool("Agent", tid="toolu_a1", description="d")])])
    folder = transcript.with_suffix("") / "subagents"
    _agent(folder, "a1", [assistant_msg("m1", iso(1), content=[tool("Grep", pattern="p")], stop_reason="tool_use"),
                          tool_result_msg("hit", iso(2)), user_msg(INTERRUPTED, iso(2)),
                          assistant_msg("m2", iso(3), content=[tool("Read", tid="t2", file_path="/a")])])
    reader = AgentReader(tmp_path)
    a = _by_id(reader.session_agents(transcript, NOW)[0])["a1"]
    assert a.tool_mix == {"Grep": 1, "Read": 1} and a.started == iso(1) and a.last == iso(3)
    session = reader._sessions[transcript.resolve().with_suffix("")]
    assert session.lead.open == {"toolu_a1"} and session.lead.size == transcript.stat().st_size


def test_a_foreground_agent_works_while_its_parent_waits_on_it(tmp_path):
    """No record while the model composes a long reply: the lead's open
    Agent call says the agent is still at work, for up to TOOL_PATIENCE."""
    transcript = write_jsonl(tmp_path / "projects" / "-x" / "s1.jsonl", [
        user_msg("go", iso(0)), assistant_msg("lead", iso(0), content=[tool("Agent", tid="toolu_a1", description="d")])])
    folder = transcript.with_suffix("") / "subagents"
    _agent(folder, "a1", [user_msg("look", iso(0)), assistant_msg("m1", iso(1), content=[tool("Grep", pattern="p")]),
                          tool_result_msg("hit", iso(2)), user_msg(INTERRUPTED, iso(2))])
    _agent(folder, "a2", [assistant_msg("m2", iso(2), content=[{"type": "text", "text": "Thinking aloud."}])],
           toolUseId="toolu_a2")
    _append(transcript, assistant_msg("lead2", iso(0), content=[tool("Agent", tid="toolu_a2", description="e")]))
    reader = AgentReader(tmp_path)
    agents = _by_id(reader.session_agents(transcript, NOW)[0])
    assert agents["a1"].state == "running"                       # 3 min quiet, its lead still waits
    assert agents["a2"].state == "running"                       # a text block mid-run is not the end
    later = NOW + timedelta(minutes=13)                          # 16 min after iso(2)
    assert _by_id(reader.session_agents(transcript, later)[0])["a1"].state == "asleep"
    _append(transcript, tool_result_msg("interrupted", iso(3), tool_use_id="toolu_a1"),
            tool_result_msg("done", iso(3), tool_use_id="toolu_a2"))
    agents = _by_id(reader.session_agents(transcript, NOW)[0])
    assert agents["a1"].state == "asleep" and agents["a2"].state == "done"   # the lead has its results


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
