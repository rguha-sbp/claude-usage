"""Per-session and month-level cost tips (spec §4.5).

Session dict keys consumed (see `Store._session_dict` / `Store.sessions()`):
session_id, title, first_prompt, project, cwd, calls, tool_calls, compactions,
total_tokens, input_tokens, cache_write_tokens, cache_read_tokens, output_tokens,
thinking_tokens, peak_context, calls_over_threshold, tokens_over_threshold,
tool_result_bytes, content_bytes, subagent_tokens, cold_restarts, rebuild_tokens,
repeated_reads (dict path -> count), cost (float or None), model_costs.

All numeric fields may legitimately be 0; `cost` may be None. Divisions are
zero-safe: content_bytes == 0 means delegate-exploration never fires;
total_tokens == 0 means the subagent share is treated as 0 (so the tip can
still fire on the tool-result ratio alone); output_tokens == 0 means
thinking-heavy never fires.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from .cost import blended_price

# Skills panel thresholds: a session whose injected skill/hook text is more
# than this share of its context, or a single skill above this share of the
# month's context, gets a tip.
SKILL_SESSION_SHARE = 0.10
SKILL_MONTH_SHARE = 0.05

# Every tip links the Claude Code doc that backs its advice. Each claim in a
# tip's text was checked against that page on 2026-11-05; keep them in step.
_DOCS = "https://code.claude.com/docs/en/"
DOC_CONTEXT = _DOCS + "costs#manage-context-proactively"
DOC_COMPACT = _DOCS + "prompt-caching#compacting-the-conversation"
DOC_CACHE = _DOCS + "prompt-caching#cache-lifetime"
DOC_SUBAGENTS = _DOCS + "sub-agents#isolate-high-volume-operations"
DOC_PROMPTS = _DOCS + "costs#write-specific-prompts"
DOC_EFFORT = _DOCS + "costs#adjust-extended-thinking"
DOC_SKILLS = _DOCS + "skills#skill-content-lifecycle"
DOC_COSTS = _DOCS + "costs#reduce-token-usage"


@dataclass
class Tip:
    id: str
    title: str
    detail: str
    saving_usd: Optional[float]
    doc: str


_UNITS = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))


def fmt_tokens(n: Any) -> str:
    """1234 -> '1.2K', 1234567 -> '1.2M', 1.3e9 -> '1.3B', below 1000 -> plain int.

    The unit is chosen AFTER rounding to one decimal: e.g. 999_950 rounds to
    "1000.0" at the K scale, which is promoted to "1.0M" rather than shown as
    "1000.0K".
    """
    n = float(n)
    if abs(n) < 1000:
        return str(int(n))
    for i, (value, suffix) in enumerate(_UNITS):
        if abs(n) >= value:
            mantissa = round(n / value, 1)
            if abs(mantissa) >= 1000 and i > 0:
                value, suffix = _UNITS[i - 1]
                mantissa = round(n / value, 1)
            return "%.1f%s" % (mantissa, suffix)
    return str(int(n))  # unreachable, kept for safety


def fmt_usd(x: float) -> str:
    """$1,234 at/above $100 (no decimals); $12.40 under $100 (two decimals).

    Rounds half-up (not Python/Decimal's default half-to-even) using Decimal
    to avoid float artifacts, and picks the format from the ALREADY-ROUNDED
    cents value so a boundary case like 99.995 (which rounds to 100.00) is
    shown as "$100", not "$99.99" / "$100.00".
    """
    cents = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if cents >= 100:
        whole = cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return "$%s" % format(int(whole), ",")
    return "$%s" % cents


def _k(threshold: int) -> str:
    return "%dK" % (threshold // 1000)


def session_tips(sess: Dict[str, Any], threshold: int) -> List[Tip]:
    tips: List[Tip] = []

    # compact-earlier
    n_over = sess.get("calls_over_threshold") or 0
    if n_over > 0:
        tokens_over = sess.get("tokens_over_threshold") or 0
        total_tokens = sess.get("total_tokens") or 0
        price = blended_price(sess.get("cost"), total_tokens)
        saving = tokens_over * price if price is not None else None
        saving_txt = " (about %s saved by compacting sooner)" % fmt_usd(saving) if saving is not None else ""
        detail = ("%d calls of %d carried more than %s of context, and every call sends the whole conversation "
                   "again. Running /compact at a natural break, such as between tasks, before %s would have "
                   "avoided re-reading about %s tokens%s." %
                   (n_over, sess.get("calls") or 0, _k(threshold), _k(threshold), fmt_tokens(tokens_over), saving_txt))
        tips.append(Tip("compact-earlier", "Compact at %s, not later" % _k(threshold), detail, saving, DOC_COMPACT))

    # delegate-exploration
    content = sess.get("content_bytes") or 0
    if content:
        total = sess.get("total_tokens") or 0
        tool_bytes = sess.get("tool_result_bytes") or 0
        ratio = tool_bytes / content
        subagent_tokens = sess.get("subagent_tokens") or 0
        subagent_share = (subagent_tokens / total) if total else 0.0
        if ratio > 0.5 and subagent_share < 0.15:
            detail = ("Tool results were %s bytes of %s bytes of context (%d%%), and only %d%% of tokens went "
                       "through subagents. A subagent does search and read-heavy work in its own context and "
                       "returns only a summary; its own calls still count toward usage."
                       % (fmt_tokens(tool_bytes), fmt_tokens(content), round(100 * ratio), round(100 * subagent_share)))
            tips.append(Tip("delegate-exploration", "Send file exploration to subagents", detail, None, DOC_SUBAGENTS))

    # cold-cache
    cold = sess.get("cold_restarts") or 0
    if cold >= 2:
        detail = ("%d gaps of over an hour outlived the prompt cache (one hour on a Claude subscription, five "
                   "minutes on an API key), so about %s tokens were written to the cache again at the cache-write "
                   "price instead of read at a tenth of the input price. /clear costs nothing when you return to "
                   "start fresh; a /compact after the gap re-reads the whole conversation first."
                   % (cold, fmt_tokens(sess.get("rebuild_tokens") or 0)))
        tips.append(Tip("cold-cache", "%d cold cache restarts" % cold, detail, None, DOC_CACHE))

    # split-session
    calls = sess.get("calls") or 0
    compactions = sess.get("compactions") or 0
    if calls > 300 and compactions <= 1:
        detail = ("This session ran %d calls with %d compaction(s). Use /clear when you switch to unrelated "
                   "work, and /compact at natural breaks, so stale context is not sent with every later call."
                   % (calls, compactions))
        tips.append(Tip("split-session", "Split this into smaller sessions", detail, None, DOC_CONTEXT))

    # repeated-reads
    reads = {p: n for p, n in (sess.get("repeated_reads") or {}).items() if n >= 3}
    if reads:
        worst = sorted(reads.items(), key=lambda kv: -kv[1])[:3]
        detail = ("These files were read repeatedly: %s. Each read adds the file to the conversation, which every "
                   "later call sends again. Specific prompts that name the file and function need fewer reads."
                   % ", ".join("%s (%d times)" % (p.rsplit("/", 1)[-1], n) for p, n in worst))
        tips.append(Tip("repeated-reads", "Same files read repeatedly", detail, None, DOC_PROMPTS))

    # thinking-heavy
    out = sess.get("output_tokens") or 0
    if out:
        thinking = sess.get("thinking_tokens") or 0
        thinking_share = thinking / out
        if thinking_share > 0.3:
            detail = ("Thinking tokens (%s) were %d%% of output tokens (%s), and thinking is billed as output. "
                       "For simpler tasks, lower the effort level with /effort."
                       % (fmt_tokens(thinking), round(100 * thinking_share), fmt_tokens(out)))
            tips.append(Tip("thinking-heavy", "Lower effort for routine work", detail, None, DOC_EFFORT))

    # skill-heavy
    carried = sess.get("skills_carried_tokens") or 0
    ctx = (sess.get("input_tokens") or 0) + (sess.get("cache_write_tokens") or 0) + (sess.get("cache_read_tokens") or 0)
    if ctx and carried / ctx > SKILL_SESSION_SHARE:
        names = ", ".join(r["name"] for r in (sess.get("skills") or [])[:3])
        detail = ("Skill and hook text carried about %s tokens, %d%% of this session's context (%s). A loaded "
                   "skill stays in the conversation and is sent with every later call, and SessionStart hook "
                   "output joins every session: load skills only where they are needed, and keep SessionStart "
                   "hooks short." % (fmt_tokens(carried), round(100 * carried / ctx), names))
        tips.append(Tip("skill-heavy", "Skills are %d%% of this session's context" % round(100 * carried / ctx),
                        detail, None, DOC_SKILLS))

    return tips


def month_tips(sessions: List[Dict[str, Any]], threshold: int, cost_available: bool,
               skills: Optional[List[Dict[str, Any]]] = None, period_phrase: str = "this month") -> List[Tip]:
    """Tips across every session of the period. `skills`: the period's rows
    from skills.aggregate_loads, heaviest first. `period_phrase` is how the
    text refers to the period: 'this month', 'today' or 'on this day'."""
    tips: List[Tip] = []
    per_session = [(s, session_tips(s, threshold)) for s in sessions]

    # a single skill / injection carrying a large share of the month's context
    top = (skills or [None])[0]
    if top and (top.get("share") or 0) > SKILL_MONTH_SHARE:
        tips.append(Tip(
            "month-skills", "%s is %d%% of the month's context" % (top["name"], round(100 * top["share"])),
            "%s was loaded %d time(s) in %d session(s), about %s tokens each, and then stayed in the "
            "conversation, sent with every later call: roughly %s tokens, %d%% of the context %s. Load it "
            "only in sessions that need it."
            % (top["name"], top["loads"], top["sessions"], fmt_tokens(top["injected_tokens"] or 0),
               fmt_tokens(top["carried_tokens"]), round(100 * top["share"]), period_phrase),
            None, DOC_SKILLS))

    # total approximate saving from compact-earlier across sessions
    over_sessions = [s for s in sessions if (s.get("calls_over_threshold") or 0) > 0]
    if over_sessions:
        saving_total = None
        if cost_available:
            saving_total = sum(
                (t.saving_usd or 0.0) for _, ts in per_session for t in ts if t.id == "compact-earlier")
        saving_txt = " Approximate saving: %s." % fmt_usd(saving_total) if saving_total is not None else ""
        tips.append(Tip(
            "month-compact", "Compact at %s across sessions" % _k(threshold),
            "%d session(s) %s carried context above %s before compacting.%s"
            % (len(over_sessions), period_phrase, _k(threshold), saving_txt),
            saving_total, DOC_COMPACT))

    # count of sessions with at least one tip
    with_tips = sum(1 for _, ts in per_session if ts)
    if with_tips:
        tips.append(Tip(
            "month-sessions", "Sessions with tips %s" % period_phrase,
            "%d of %d sessions %s have at least one tip." % (with_tips, len(sessions), period_phrase),
            None, DOC_COSTS))

    # count of sessions that would benefit from delegating exploration
    delegate_count = sum(1 for _, ts in per_session if any(t.id == "delegate-exploration" for t in ts))
    if delegate_count:
        tips.append(Tip(
            "month-delegate", "Delegate exploration to subagents",
            "%d of %d sessions %s spent most of their context on tool results with little subagent "
            "use; sending file exploration to subagents would keep that reading out of the main window."
            % (delegate_count, len(sessions), period_phrase),
            None, DOC_SUBAGENTS))

    return tips
