from datetime import date
from pathlib import Path

from tests.helpers import assistant_msg, iso, user_msg
from usage.parser import parse_records
from usage.projection import compute_period, month_range, pace_7d, period_range, prev_month, prev_period, trend
from usage.store import Store, local_day


def test_prev_month_and_range():
    assert prev_month("2026-09") == "2026-08"
    assert prev_month("2026-01") == "2025-12"
    assert month_range("2026-09") == (date(2026, 9, 1), date(2026, 9, 30))
    assert month_range("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))


def test_prev_period_and_period_range_handle_months_and_days():
    assert prev_period("2026-09") == "2026-08"
    assert prev_period("2026-09-01") == "2026-08-31"
    assert period_range("2026-09") == (date(2026, 9, 1), date(2026, 9, 30))
    assert period_range("2026-09-14") == (date(2026, 9, 14), date(2026, 9, 14))


def test_pace_7d_counts_missing_days_as_zero():
    vals = {"2026-09-07": 70.0, "2026-09-08": 0.0, "2026-09-01": 999.0}   # 09-01 outside window
    assert pace_7d(vals, date(2026, 9, 8)) == 10.0


def test_trend_bands_and_no_comparison():
    up = trend(130.0, 100.0, 31, "ratio", "up", "down", "flat")
    assert up.direction == "up" and round(up.delta, 2) == 0.30 and up.verdict == "up"
    assert trend(96.0, 100.0, 31, "ratio", "u", "d", "f").direction == "flat"
    assert trend(94.0, 100.0, 31, "ratio", "u", "d", "f").direction == "down"
    pts = trend(0.63, 0.71, 31, "points", "u", "d", "f")
    assert pts.direction == "down" and round(pts.delta, 2) == -0.08
    assert trend(0.63, 0.62, 31, "points", "u", "d", "f").direction == "flat"
    none = trend(130.0, 100.0, 5, "ratio", "u", "d", "f")
    assert none.direction == "none" and none.verdict == "No comparison yet"
    assert trend(130.0, None, 31, "ratio", "u", "d", "f").direction == "none"


def _seed(s: Store, base: str, sid: str, cache_read: int, cwd="/x/repo"):
    s.upsert_chunk(parse_records([user_msg("q", iso(0, base), cwd=cwd),
                                  assistant_msg("m-" + sid, iso(1, base), cache_read=cache_read, cwd=cwd)], sid))


def test_compute_month_with_costs():
    s = Store(Path(":memory:"))
    _seed(s, "2026-09-01T12:00:00.000Z", "s1", 300000)     # over 200K
    _seed(s, "2026-09-02T12:00:00.000Z", "s2", 50000)
    _seed(s, "2026-08-10T12:00:00.000Z", "s0", 250000)     # previous month
    s.recompute(threshold=200000)
    d1, d2, d0 = local_day(iso(1, "2026-09-01T12:00:00.000Z")), local_day(iso(1, "2026-09-02T12:00:00.000Z")), local_day(iso(1, "2026-08-10T12:00:00.000Z"))
    s.set_daily_costs([(d1, "*", 30.0), (d1, "claude-fable-5", 30.0), (d2, "*", 5.0), (d2, "claude-fable-5", 5.0), (d0, "*", 100.0)])
    s.set_session_costs([("s1", 30.0, {}), ("s2", 5.0, {}), ("s0", 100.0, {})])
    m = compute_period(s, "2026-09", date(2026, 9, 8), 200000, cost_available=True)
    assert m.unit == "usd" and m.to_date == 35.0
    assert m.pace == 5.0 / 7          # 7-day window 09-02..09-08 holds only day 2
    assert m.projected == 35.0 + m.pace * 22
    assert m.previous_total == 100.0 and m.previous_period == "2026-08"
    assert m.spend_trend.direction == "none"             # previous month has 1 day of data (<7)
    # s1: context 300002, over by 100002 -> share of day-1 context ~ 1/3 -> over_value ~ 10
    assert round(m.over_value, 1) == 10.0
    assert round(m.over_share, 3) == round(10.0 / 35.0, 3)
    assert m.top_session["session_id"] == "s1" and m.top_session["value"] == 30.0
    assert len(m.series) == 30 and m.series[0].projected is False and m.series[-1].projected is True
    assert m.series[7].projected is False and m.series[8].projected is True
    assert m.model_split == {"claude-fable-5": 35.0}
    assert m.token_split["cache_read"] == 350000


def test_compute_month_without_costs_uses_tokens():
    s = Store(Path(":memory:"))
    _seed(s, "2026-09-01T12:00:00.000Z", "s1", 1000)
    s.recompute(threshold=200000)
    m = compute_period(s, "2026-09", date(2026, 9, 8), 200000, cost_available=False)
    assert m.unit == "tokens" and m.to_date == 2 + 1000 + 10
    assert m.top_session["value"] == 1012
    assert m.model_split == {"claude-fable-5": 1012}


def test_pace_7d_spans_previous_month():
    s = Store(Path(":memory:"))
    days = ["2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31",
            "2026-09-01", "2026-09-02", "2026-09-03"]
    for i, day in enumerate(days):
        _seed(s, day + "T12:00:00.000Z", "s" + str(i), cache_read=(i + 1) * 100)
    s.recompute(threshold=200000)
    # each seeded day totals 2 (input) + (i+1)*100 (cache_read) + 10 (output) tokens
    totals = [2 + (i + 1) * 100 + 10 for i in range(len(days))]

    # window Aug28..Sep3 spans the month boundary: all 7 seeded days count.
    m_early = compute_period(s, "2026-09", date(2026, 9, 3), 200000, cost_available=False)
    assert m_early.pace == sum(totals) / 7.0

    # window Sep2..Sep8 is fully inside September: only Sep2 and Sep3 have data.
    m_late = compute_period(s, "2026-09", date(2026, 9, 8), 200000, cost_available=False)
    assert m_late.pace == (totals[5] + totals[6]) / 7.0


def test_previous_days_counts_zero_token_day():
    s = Store(Path(":memory:"))
    _seed(s, "2026-08-01T12:00:00.000Z", "sA", cache_read=1000)
    _seed(s, "2026-08-02T12:00:00.000Z", "sB", cache_read=2000)
    s.upsert_chunk(parse_records(
        [user_msg("q", iso(0, "2026-08-03T12:00:00.000Z"), cwd="/x/repo"),
         assistant_msg("m-sC", iso(1, "2026-08-03T12:00:00.000Z"), input_tokens=0,
                       cache_write=0, cache_read=0, output=0, cwd="/x/repo")], "sC"))
    s.recompute(threshold=200000)
    m = compute_period(s, "2026-09", date(2026, 9, 8), 200000, cost_available=False)
    assert m.previous_days == 3
    assert m.spend_trend.direction == "none"


def test_top_session_share_zero_when_to_date_zero():
    s = Store(Path(":memory:"))
    _seed(s, "2026-09-05T12:00:00.000Z", "s1", 1000)
    s.recompute(threshold=200000)
    d = local_day(iso(1, "2026-09-05T12:00:00.000Z"))
    s.set_daily_costs([(d, "*", 0.0), (d, "claude-fable-5", 0.0)])
    s.set_session_costs([("s1", 0.0, {})])
    m = compute_period(s, "2026-09", date(2026, 9, 8), 200000, cost_available=True)
    assert m.to_date == 0.0
    assert m.top_session["session_id"] == "s1"
    assert m.top_session["value"] == 0.0
    assert m.top_session["share"] == 0.0


def _seed_at(s: Store, ts: str, sid: str, cache_read: int) -> None:
    _seed(s, ts, sid, cache_read)


def _local_hour(ts: str) -> int:
    from datetime import datetime
    return datetime.fromisoformat(ts).astimezone().hour


def test_compute_period_for_a_day_gives_hourly_series_and_compares_to_the_previous_day():
    s = Store(Path(":memory:"))
    a, b = "2026-09-14T06:00:00.000Z", "2026-09-14T09:00:00.000Z"     # two hours of the day
    _seed(s, a, "s1", 300000)                                         # over 200K
    _seed(s, b, "s2", 100000)
    _seed(s, "2026-09-13T12:00:00.000Z", "s0", 50000)                 # the day before
    s.recompute(threshold=200000)
    day, prev = local_day(iso(1, a)), local_day(iso(1, "2026-09-13T12:00:00.000Z"))
    s.set_daily_costs([(day, "*", 40.0), (day, "claude-fable-5", 40.0), (prev, "*", 10.0), (prev, "claude-fable-5", 10.0)])
    s.set_session_costs([("s1", 30.0, {}), ("s2", 10.0, {}), ("s0", 10.0, {})])

    m = compute_period(s, day, date.fromisoformat(day), 200000, cost_available=True)
    assert m.kind == "day" and m.period == day and m.month == day[:7]
    assert m.to_date == 40.0 and m.projected == 40.0 and m.pace == 40.0
    assert m.days_elapsed == 1 and m.days_in_period == 1
    assert m.previous_period == prev and m.previous_total == 10.0 and m.previous_days == 1
    assert m.spend_trend.direction == "up" and "yesterday" in m.spend_trend.verdict
    assert [pt.label for pt in m.series] == ["%02d" % h for h in range(24)]
    assert not any(pt.projected for pt in m.series)
    by_hour = {pt.label: pt for pt in m.series}
    ha, hb = "%02d" % _local_hour(iso(1, a)), "%02d" % _local_hour(iso(1, b))
    # the day's cost is split across its hours by weighted tokens
    assert round(by_hour[ha].value + by_hour[hb].value, 6) == 40.0
    assert by_hour[ha].value > by_hour[hb].value > 0
    assert by_hour[ha].over_tokens == 100002 and by_hour[hb].over_tokens == 0
    assert sum(pt.value for pt in m.series if pt.label not in (ha, hb)) == 0
    assert m.top_session["session_id"] == "s1"
    assert m.model_split == {"claude-fable-5": 40.0}


def test_compute_period_for_a_day_without_a_previous_day_has_no_comparison():
    s = Store(Path(":memory:"))
    _seed(s, "2026-09-14T06:00:00.000Z", "s1", 1000)
    s.recompute(threshold=200000)
    day = local_day(iso(1, "2026-09-14T06:00:00.000Z"))
    m = compute_period(s, day, date(2026, 9, 20), 200000, cost_available=False)
    assert m.unit == "tokens" and m.to_date == 1012 and m.previous_days == 0
    assert m.spend_trend.direction == "none" and m.over_trend.direction == "none"


def test_compute_period_for_an_older_day_names_the_previous_weekday():
    s = Store(Path(":memory:"))
    _seed(s, "2026-09-14T06:00:00.000Z", "s1", 1000)
    _seed(s, "2026-09-13T06:00:00.000Z", "s0", 3000)
    s.recompute(threshold=200000)
    day = local_day(iso(1, "2026-09-14T06:00:00.000Z"))
    m = compute_period(s, day, date(2026, 9, 20), 200000, cost_available=False)
    assert m.spend_trend.direction == "down"
    assert "Sunday 13 September" in m.spend_trend.verdict
