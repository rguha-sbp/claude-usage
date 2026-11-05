"""Period totals, projection and trends (spec Sec 4.4 and Sec 11).

A period is a month 'YYYY-MM' or a day 'YYYY-MM-DD'. A month is projected to
its end from the last seven days' pace and compared with the previous month;
a day is a closed total compared with the previous day, and its chart series
is per hour instead of per day.
"""
import calendar
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .store import Store, weighted

MIN_PREVIOUS_DAYS = 7
RATIO_BAND = 0.05
POINTS_BAND = 0.02
HOURS = ["%02d" % h for h in range(24)]


@dataclass
class Trend:
    direction: str
    verdict: str
    delta: float | None


@dataclass
class Point:
    """One bar of the chart: a day of the month, or an hour of the day."""
    label: str
    value: float
    over_value: float
    projected: bool
    tokens: int
    over_tokens: int


@dataclass
class PeriodSummary:
    period: str
    kind: str                 # "month" | "day"
    month: str                # the containing month
    unit: str
    to_date: float
    projected: float
    pace: float
    days_elapsed: int
    days_in_period: int
    previous_period: str
    previous_total: float | None
    previous_days: int
    spend_trend: Trend
    over_value: float
    over_share: float | None
    previous_over_share: float | None
    over_trend: Trend
    top_session: dict[str, Any] | None
    series: list[Point]
    model_split: dict[str, float]
    token_split: dict[str, int]


def kind_of(period: str) -> str:
    return "day" if len(period) == 10 else "month"


def prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return "%04d-%02d" % ((y - 1, 12) if m == 1 else (y, m - 1))


def month_range(month: str) -> tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def prev_period(period: str) -> str:
    if kind_of(period) == "day":
        return (date.fromisoformat(period) - timedelta(days=1)).isoformat()
    return prev_month(period)


def period_range(period: str) -> tuple[date, date]:
    if kind_of(period) == "day":
        d = date.fromisoformat(period)
        return d, d
    return month_range(period)


def pace_7d(values_by_day: dict[str, float], today: date) -> float:
    days = [(today - timedelta(days=i)).isoformat() for i in range(7)]
    return sum(values_by_day.get(d, 0.0) for d in days) / 7.0


def trend(current: float | None, previous: float | None, previous_days: int,
          kind: str, label_up: str, label_down: str, label_flat: str,
          min_days: int = MIN_PREVIOUS_DAYS) -> Trend:
    if current is None or previous is None or previous_days < min_days:
        return Trend("none", "No comparison yet", None)
    if kind == "ratio":
        if previous == 0:
            return Trend("none", "No comparison yet", None)
        delta = (current - previous) / previous
        band = RATIO_BAND
    else:
        delta = current - previous
        band = POINTS_BAND
    if delta > band:
        return Trend("up", label_up, delta)
    if delta < -band:
        return Trend("down", label_down, delta)
    return Trend("flat", label_flat, delta)


@dataclass
class Buckets:
    """Rollup rows of one period summed per bucket (a day of a month, or an
    hour of a day), plus the period's model and token splits."""
    value: dict[str, float]
    over: dict[str, float]
    tokens: dict[str, int]
    over_tokens: dict[str, int]
    model_split: dict[str, float]
    token_split: dict[str, int]
    days_with_data: int

    def total(self) -> float:
        return sum(self.value.values())


def _bucket(store: Store, period: str, cost_available: bool) -> Buckets:
    rows = store.rollup(period)
    by_hour = kind_of(period) == "day"
    key: Callable[[dict[str, Any]], str] = (lambda r: "%02d" % r["hour"]) if by_hour else (lambda r: r["day"])
    ctx: dict[str, int] = {}
    tokens: dict[str, int] = {}
    over_tokens: dict[str, int] = {}
    weight: dict[str, float] = {}
    model_tokens: dict[str, float] = {}
    token_split = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    days_seen = set()
    for r in rows:
        k = key(r)
        days_seen.add(r["day"])
        c = r["input_tokens"] + r["cache_write_tokens"] + r["cache_read_tokens"]
        ctx[k] = ctx.get(k, 0) + c
        tokens[k] = tokens.get(k, 0) + c + r["output_tokens"]
        over_tokens[k] = over_tokens.get(k, 0) + r["tokens_over_threshold"]
        weight[k] = weight.get(k, 0.0) + weighted(r["input_tokens"], r["cache_write_tokens"],
                                                 r["cache_read_tokens"], r["output_tokens"])
        model_tokens[r["model"]] = model_tokens.get(r["model"], 0) + c + r["output_tokens"]
        token_split["input"] += r["input_tokens"]
        token_split["cache_write"] += r["cache_write_tokens"]
        token_split["cache_read"] += r["cache_read_tokens"]
        token_split["output"] += r["output_tokens"]
    if cost_available:
        costs = store.daily_costs(period)
        model_split: dict[str, float] = {}
        for v in costs.values():
            for m, c in v.items():
                if m != "*":
                    model_split[m] = model_split.get(m, 0.0) + c
        if by_hour:
            # ccusage prices per day; an hour gets the day's share by weighted tokens.
            day_total = sum(v.get("*", 0.0) for v in costs.values())
            weight_total = sum(weight.values())
            value = {k: day_total * w / weight_total for k, w in weight.items()} if weight_total else {}
        else:
            # A day with usage but no cost entry yet contributes 0.0.
            value = {d: v.get("*", 0.0) for d, v in costs.items()}
    else:
        value = {k: float(t) for k, t in tokens.items()}
        model_split = dict(model_tokens)
    over = {k: (value.get(k, 0.0) * over_tokens.get(k, 0) / ctx[k]) if ctx.get(k) else 0.0
            for k in set(value) | set(ctx)}
    return Buckets(value, over, tokens, over_tokens, model_split, token_split, len(days_seen))


def _previous_name(period: str, previous: str, today: date) -> str:
    """How the verdicts refer to the previous period: 'August', 'yesterday'
    or 'Sunday 13 September'."""
    if kind_of(period) == "month":
        return date(int(previous[:4]), int(previous[5:7]), 1).strftime("%B")
    d = date.fromisoformat(previous)
    return "yesterday" if d == today - timedelta(days=1) else d.strftime("%A %-d %B")


def compute_period(store: Store, period: str, today: date, threshold: int, cost_available: bool) -> PeriodSummary:
    kind = kind_of(period)
    first, last = period_range(period)
    unit = "usd" if cost_available else "tokens"
    cur = _bucket(store, period, cost_available)
    previous = prev_period(period)
    prev = _bucket(store, previous, cost_available)

    to_date = cur.total()
    over_value = sum(cur.over.values())
    cutoff = min(today, last)
    days_elapsed = max(0, (cutoff - first).days + 1) if today >= first else 0
    if kind == "month":
        remaining = max(0, (last - cutoff).days) if today >= first else (last - first).days + 1
        # pace_7d's trailing window (today and the 6 days before it) can span
        # into the previous month, so merge in its per-day values (spec Sec 4.4).
        pace_source = dict(prev.value)
        pace_source.update(cur.value)
        pace = pace_7d(pace_source, cutoff) if today >= first else 0.0
        projected = to_date + pace * remaining
        min_days = MIN_PREVIOUS_DAYS
    else:
        pace = projected = to_date          # a day is a closed total; nothing is projected
        min_days = 1

    previous_total = prev.total() if prev.days_with_data else None
    previous_over_share = (sum(prev.over.values()) / previous_total) if previous_total else None
    over_share = (over_value / to_date) if to_date else None

    prev_name = _previous_name(period, previous, today)
    unit_word = "Spending" if cost_available else "Token use"
    spend_trend = trend(projected, previous_total, prev.days_with_data, "ratio",
                        "%s is running above %s" % (unit_word, prev_name),
                        "%s is running below %s" % (unit_word, prev_name),
                        "%s is level with %s" % (unit_word, prev_name), min_days)
    if spend_trend.delta is not None and spend_trend.direction != "flat":
        pct = abs(round(spend_trend.delta * 100))
        spend_trend.verdict = spend_trend.verdict.replace("running", "running %d%%" % pct)
    k = "%dK" % (threshold // 1000)
    over_trend = trend(over_share, previous_over_share, prev.days_with_data, "points",
                       "More of your spend is happening above %s context" % k,
                       "Less of your spend is happening above %s context" % k,
                       "Share above %s context is unchanged" % k, min_days)

    sessions = store.sessions(period)
    top = None
    if sessions:
        key = (lambda s: s.get("cost") or 0.0) if cost_available else (lambda s: s["total_tokens"])
        best = max(sessions, key=key)
        v = key(best)
        top = {"session_id": best["session_id"],
               "title": best.get("title") or best.get("first_prompt") or best["session_id"],
               "value": v, "share": (v / to_date) if to_date else 0.0}

    series: list[Point] = []
    if kind == "month":
        d = first
        while d <= last:
            ds = d.isoformat()
            projected_day = d > cutoff if today >= first else True
            series.append(Point(label=ds, value=pace if projected_day else cur.value.get(ds, 0.0),
                                over_value=0.0 if projected_day else cur.over.get(ds, 0.0),
                                projected=projected_day, tokens=cur.tokens.get(ds, 0),
                                over_tokens=cur.over_tokens.get(ds, 0)))
            d += timedelta(days=1)
    else:
        series = [Point(label=h, value=cur.value.get(h, 0.0), over_value=cur.over.get(h, 0.0), projected=False,
                        tokens=cur.tokens.get(h, 0), over_tokens=cur.over_tokens.get(h, 0)) for h in HOURS]

    return PeriodSummary(period=period, kind=kind, month=period[:7], unit=unit, to_date=to_date,
                         projected=projected, pace=pace, days_elapsed=days_elapsed,
                         days_in_period=(last - first).days + 1, previous_period=previous,
                         previous_total=previous_total, previous_days=prev.days_with_data,
                         spend_trend=spend_trend, over_value=over_value, over_share=over_share,
                         previous_over_share=previous_over_share, over_trend=over_trend,
                         top_session=top, series=series, model_split=cur.model_split, token_split=cur.token_split)
