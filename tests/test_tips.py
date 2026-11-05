from usage.tips import Tip, fmt_tokens, fmt_usd, month_tips, session_tips


def base(**over):
    s = dict(session_id="s", title="T", calls=10, compactions=0, calls_over_threshold=0, tokens_over_threshold=0,
             tool_result_bytes=100, content_bytes=1000, subagent_tokens=0, total_tokens=100000,
             cold_restarts=0, repeated_reads={}, thinking_tokens=0, output_tokens=1000, cost=None,
             peak_context=50000)
    s.update(over)
    return s


def test_fmt_tokens():
    assert fmt_tokens(950) == "950"
    assert fmt_tokens(1234) == "1.2K"
    assert fmt_tokens(1234567) == "1.2M"
    assert fmt_tokens(1300000000) == "1.3B"


def test_fmt_tokens_unit_promotion():
    assert fmt_tokens(999) == "999"
    assert fmt_tokens(1000) == "1.0K"
    assert fmt_tokens(999_950) == "1.0M"
    assert fmt_tokens(999_999_999) == "1.0B"
    assert fmt_tokens(1_300_000_000) == "1.3B"


def test_fmt_usd():
    assert fmt_usd(0) == "$0.00"
    assert fmt_usd(12.4) == "$12.40"
    assert fmt_usd(99.994) == "$99.99"
    assert fmt_usd(99.995) == "$100"
    assert fmt_usd(100.5) == "$101"
    assert fmt_usd(1234.5) == "$1,235"
    assert fmt_usd(1610.3) == "$1,610"


def test_no_tips_for_clean_session():
    assert session_tips(base(), 200000) == []


def test_compact_earlier_with_and_without_cost():
    t = session_tips(base(calls_over_threshold=5, tokens_over_threshold=1000000, cost=None), 200000)
    assert [x.id for x in t] == ["compact-earlier"] and t[0].saving_usd is None
    t2 = session_tips(base(calls_over_threshold=5, tokens_over_threshold=1000000, cost=10.0, total_tokens=2000000), 200000)
    assert t2[0].saving_usd == 5.0
    assert "5 calls" in t2[0].detail and "200K" in t2[0].detail


def test_compact_earlier_boundary():
    # calls_over_threshold == 0 must not fire
    assert session_tips(base(calls_over_threshold=0, tokens_over_threshold=0), 200000) == []
    # calls_over_threshold == 1 fires
    ids = [x.id for x in session_tips(base(calls_over_threshold=1, tokens_over_threshold=1), 200000)]
    assert ids == ["compact-earlier"]


def test_delegate_exploration_thresholds():
    assert [x.id for x in session_tips(base(tool_result_bytes=600, content_bytes=1000, subagent_tokens=1000), 200000)] == ["delegate-exploration"]
    assert session_tips(base(tool_result_bytes=600, content_bytes=1000, subagent_tokens=20000), 200000) == []
    assert session_tips(base(tool_result_bytes=400, content_bytes=1000), 200000) == []


def test_delegate_exploration_boundary_ratio():
    # exactly 0.5 must not fire (needs strictly >)
    assert session_tips(base(tool_result_bytes=500, content_bytes=1000, subagent_tokens=0), 200000) == []
    # just above 0.5 fires
    ids = [x.id for x in session_tips(base(tool_result_bytes=501, content_bytes=1000, subagent_tokens=0), 200000)]
    assert ids == ["delegate-exploration"]


def test_delegate_exploration_boundary_subagent_share():
    # exactly 0.15 share must not fire (needs strictly <)
    assert session_tips(base(tool_result_bytes=600, content_bytes=1000, total_tokens=100000, subagent_tokens=15000), 200000) == []
    # just below 0.15 fires
    ids = [x.id for x in session_tips(base(tool_result_bytes=600, content_bytes=1000, total_tokens=100000, subagent_tokens=14999), 200000)]
    assert ids == ["delegate-exploration"]


def test_delegate_exploration_zero_division_guards():
    # content_bytes == 0 -> never fires, regardless of tool_result_bytes
    assert session_tips(base(tool_result_bytes=100, content_bytes=0), 200000) == []
    # total_tokens == 0 -> subagent share treated as 0, so tip can still fire on ratio alone
    ids = [x.id for x in session_tips(base(tool_result_bytes=600, content_bytes=1000, total_tokens=0, subagent_tokens=0), 200000)]
    assert ids == ["delegate-exploration"]


def test_cold_cache_split_reads_thinking():
    ids = [x.id for x in session_tips(base(cold_restarts=2, calls=301, compactions=1,
                                           repeated_reads={"/a.py": 3, "/b.py": 2},
                                           thinking_tokens=400, output_tokens=1000), 200000)]
    assert ids == ["cold-cache", "split-session", "repeated-reads", "thinking-heavy"]
    assert session_tips(base(cold_restarts=1), 200000) == []
    assert session_tips(base(calls=301, compactions=2), 200000) == []
    assert session_tips(base(thinking_tokens=300, output_tokens=1000), 200000) == []


def test_split_session_boundary():
    # calls == 300 must not fire
    assert session_tips(base(calls=300, compactions=0), 200000) == []
    # calls == 301, compactions == 1 fires (<=1)
    ids = [x.id for x in session_tips(base(calls=301, compactions=1), 200000)]
    assert ids == ["split-session"]


def test_repeated_reads_boundary():
    # count == 2 must not fire
    assert session_tips(base(repeated_reads={"/a.py": 2}), 200000) == []
    # count == 3 fires
    ids = [x.id for x in session_tips(base(repeated_reads={"/a.py": 3}), 200000)]
    assert ids == ["repeated-reads"]


def test_thinking_heavy_boundary():
    # exactly 0.3 must not fire (needs strictly >)
    assert session_tips(base(thinking_tokens=300, output_tokens=1000), 200000) == []
    # just above 0.3 fires
    ids = [x.id for x in session_tips(base(thinking_tokens=301, output_tokens=1000), 200000)]
    assert ids == ["thinking-heavy"]


def test_thinking_heavy_zero_division_guard():
    # output_tokens == 0 -> never fires, regardless of thinking_tokens
    assert session_tips(base(thinking_tokens=100, output_tokens=0), 200000) == []


def test_month_tips():
    sessions = [base(session_id="a", calls_over_threshold=3, tokens_over_threshold=100000, cost=10.0,
                     total_tokens=1000000, peak_context=500000),
                base(session_id="b"),
                base(session_id="c", cold_restarts=3)]
    tips = month_tips(sessions, 200000, cost_available=True)
    ids = [t.id for t in tips]
    assert "month-compact" in ids
    # the tool alerts, it never tells you to make Claude Code compact on its own
    assert "month-auto-compact" not in ids
    mc = [t for t in tips if t.id == "month-compact"][0]
    assert mc.saving_usd == 1.0
    assert "2 of 3 sessions this month" in [t for t in tips if t.id == "month-sessions"][0].detail
    today = month_tips(sessions, 200000, cost_available=True, period_phrase="today")
    assert "2 of 3 sessions today" in [t for t in today if t.id == "month-sessions"][0].detail
    assert "today" in [t for t in today if t.id == "month-compact"][0].detail
    assert month_tips([base()], 200000, cost_available=False) == []


def test_month_tips_delegate_exploration_count():
    sessions = [base(session_id="a", tool_result_bytes=600, content_bytes=1000, subagent_tokens=0),
                base(session_id="b")]
    tips = month_tips(sessions, 200000, cost_available=True)
    md = [t for t in tips if t.id == "month-delegate"]
    assert len(md) == 1
    assert "1 of 2 sessions" in md[0].detail


def test_month_tips_saving_without_cost():
    sessions = [base(session_id="a", calls_over_threshold=3, tokens_over_threshold=100000, cost=10.0,
                     total_tokens=1000000)]
    tips = month_tips(sessions, 200000, cost_available=False)
    mc = [t for t in tips if t.id == "month-compact"][0]
    assert mc.saving_usd is None


def _skill_sess(carried, ctx=100_000):
    return {"session_id": "s1", "calls": 10, "input_tokens": ctx, "cache_write_tokens": 0,
            "cache_read_tokens": 0, "output_tokens": 100, "total_tokens": ctx + 100,
            "skills_carried_tokens": carried,
            "skills": [{"name": "superpowers:brainstorming", "kind": "skill", "loads": 1,
                        "injected_tokens": 6_000, "carried_tokens": carried}]}


def test_skill_heavy_session_tip_fires_above_ten_percent_of_context():
    tips = {t.id: t for t in session_tips(_skill_sess(15_000), 200000)}
    assert "skill-heavy" in tips
    assert "superpowers:brainstorming" in tips["skill-heavy"].detail and "15%" in tips["skill-heavy"].detail
    assert "skill-heavy" not in {t.id for t in session_tips(_skill_sess(5_000), 200000)}
    assert "skill-heavy" not in {t.id for t in session_tips(_skill_sess(0, ctx=0), 200000)}


def test_month_skills_tip_names_the_heaviest_skill():
    skills = [{"name": "superpowers:brainstorming", "kind": "skill", "loads": 14, "sessions": 9,
               "injected_tokens": 5_751, "exact": False, "carried_tokens": 900_000, "share": 0.09},
              {"name": "code-review", "kind": "skill", "loads": 2, "sessions": 2,
               "injected_tokens": 367, "exact": False, "carried_tokens": 1_000, "share": 0.0001}]
    tips = {t.id: t for t in month_tips([], 200000, cost_available=False, skills=skills)}
    assert "month-skills" in tips
    assert "superpowers:brainstorming" in tips["month-skills"].detail and "9%" in tips["month-skills"].detail
    skills[0]["share"] = 0.02
    assert "month-skills" not in {t.id for t in month_tips([], 200000, cost_available=False, skills=skills)}
    assert "month-skills" not in {t.id for t in month_tips([], 200000, cost_available=False)}
