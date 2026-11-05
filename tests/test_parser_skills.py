"""Skill loads and injected context (skills panel)."""
from tests.helpers import (
    assistant_msg, attachment_msg, compact_summary, hook_context_attachment, invoked_skills_attachment, iso,
    skill_block, skill_listing_attachment, tool_result_msg, user_msg,
)
from usage.parser import SkillLoad, parse_records


def test_skill_tool_use_is_recorded_once_per_message():
    a = parse_records([
        user_msg("hi", iso(0)),
        # streaming snapshots: the same message id appears twice with the same block
        assistant_msg("m1", iso(1), content=[skill_block("superpowers:brainstorming")]),
        assistant_msg("m1", iso(1), content=[skill_block("superpowers:brainstorming")], output=40),
        tool_result_msg("Launching skill: superpowers:brainstorming", iso(2)),
        assistant_msg("m2", iso(3), content=[skill_block("code-review", "sk2")]),
    ], "s1")
    assert a.skill_loads == [
        SkillLoad(load_id="m1", name="superpowers:brainstorming", kind="skill", ts=iso(1),
                  sidechain=False, body_bytes=None),
        SkillLoad(load_id="m2", name="code-review", kind="skill", ts=iso(3),
                  sidechain=False, body_bytes=None),
    ]


def test_skill_block_without_name_or_id_is_ignored():
    a = parse_records([
        assistant_msg("m1", iso(1), content=[{"type": "tool_use", "id": "x", "name": "Skill", "input": {}}]),
        assistant_msg("m2", iso(2), content=[{"type": "tool_use", "id": "x", "name": "Skill", "input": {"skill": 7}}]),
    ], "s1")
    assert a.skill_loads == []


def test_sidechain_skill_load_carries_sidechain_flag():
    a = parse_records([assistant_msg("m1", iso(1), content=[skill_block("x")])], "s1", sidechain=True)
    assert a.skill_loads[0].sidechain is True


def test_invoked_skills_attachment_records_exact_body_size():
    body = "Base directory for this skill: /x\n" + "y" * 4000
    a = parse_records([
        assistant_msg("m1", iso(1), content=[skill_block("superpowers:brainstorming")]),
        attachment_msg(invoked_skills_attachment({"superpowers:brainstorming": body}), iso(2)),
    ], "s1")
    assert a.skill_bodies == {"superpowers:brainstorming": len(body)}


def test_invoked_skills_attachment_keeps_largest_body_and_skips_malformed():
    a = parse_records([
        attachment_msg(invoked_skills_attachment({"a": "x" * 10}), iso(1)),
        attachment_msg(invoked_skills_attachment({"a": "x" * 30}), iso(2)),
        attachment_msg({"type": "invoked_skills", "skills": [{"name": 5, "content": "x"}, "junk", {"name": "b"}]}, iso(3)),
        attachment_msg({"type": "invoked_skills", "skills": "not-a-list"}, iso(4)),
    ], "s1")
    assert a.skill_bodies == {"a": 30}


def test_hook_and_listing_attachments_become_loads_with_exact_bytes():
    hook = "<EXTREMELY_IMPORTANT>You have superpowers." * 20
    listing = "- build-gap-agent: Use when ..." * 10
    recs = [
        attachment_msg({"type": "hook_success", "hookName": "SessionStart:startup", "stdout": "..."}, iso(0)),
        attachment_msg(hook_context_attachment(hook), iso(0)),
        attachment_msg(skill_listing_attachment(listing), iso(0)),
        assistant_msg("m1", iso(1)),
    ]
    a = parse_records(recs, "s1")
    assert [(l.name, l.kind, l.body_bytes, l.ts) for l in a.skill_loads] == [
        ("SessionStart hook", "hook", len(hook), iso(0)),
        ("Skill listing", "listing", len(listing), iso(0)),
    ]
    assert a.skill_loads[0].load_id == recs[1]["uuid"]


def test_hook_context_without_preceding_hook_success_gets_generic_name():
    a = parse_records([attachment_msg(hook_context_attachment("abc"), iso(0))], "s1")
    assert [(l.name, l.kind) for l in a.skill_loads] == [("Hook context", "hook")]


def test_hook_attachment_with_string_content_and_hook_success_not_double_counted():
    a = parse_records([
        attachment_msg({"type": "hook_success", "hookName": "SessionStart:startup", "stdout": "x" * 500}, iso(0)),
        attachment_msg({"type": "hook_additional_context", "content": "abc"}, iso(0)),
        attachment_msg({"type": "hook_additional_context", "content": 12}, iso(0)),
    ], "s1")
    assert [(l.kind, l.body_bytes) for l in a.skill_loads] == [("hook", 3)]


def test_merge_from_combines_skill_data():
    a = parse_records([assistant_msg("m1", iso(1), content=[skill_block("x")])], "s1")
    b = parse_records([
        assistant_msg("m2", iso(2), content=[skill_block("y")]),
        attachment_msg(invoked_skills_attachment({"y": "zz"}), iso(3)),
    ], "s1")
    a.merge_from(b)
    assert [l.name for l in a.skill_loads] == ["x", "y"]
    assert a.skill_bodies == {"y": 2}


def test_agent_id_is_stamped_on_calls_and_loads_and_compactions_are_collected():
    a = parse_records([
        assistant_msg("m1", iso(1), content=[skill_block("sk")]),
        compact_summary(iso(4)),
        assistant_msg("m2", iso(5)),
    ], "s1", sidechain=True, agent_id="agent-77")
    assert [c.agent_id for c in a.calls] == ["agent-77", "agent-77"]
    assert a.skill_loads[0].agent_id == "agent-77"
    assert a.compactions_at == [("agent-77", iso(4))]
    main = parse_records([compact_summary(iso(0))], "s1")
    assert main.calls == [] and main.compactions_at == [("", iso(0))]


def test_merge_from_combines_compactions():
    a = parse_records([compact_summary(iso(0))], "s1")
    a.merge_from(parse_records([compact_summary(iso(9))], "s1", agent_id="x"))
    assert a.compactions_at == [("", iso(0)), ("x", iso(9))]
