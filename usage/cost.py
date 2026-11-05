"""Dollar figures from ccusage (spec §4.3). Verified against ccusage 20.0.20 JSON shapes."""
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from .store import Store, UNKNOWN_PROJECT

type Runner = Callable[[list[str]], tuple[int, str, str]]


def default_runner(cmd: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", "command not found: %s" % e
    except subprocess.TimeoutExpired:
        return 124, "", "ccusage timed out after 180s"
    except Exception as e:  # never raise -- any other spawn failure is a runner failure
        return 1, "", str(e)


@dataclass
class SessionCost:
    session_id: str
    cost: float
    model_costs: dict[str, float]


@dataclass
class DailyCost:
    day: str
    cost: float
    model_costs: dict[str, float]


@dataclass
class CostResult:
    available: bool
    sessions: list[SessionCost]
    daily: list[DailyCost]
    error: str | None


def _coerce_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _breakdown(items) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(items, list):
        return out
    for b in items:
        if not isinstance(b, dict):
            continue
        name = b.get("modelName") or b.get("model")
        if name:
            out[str(name)] = _coerce_float(b.get("cost"))
    return out


def parse_session_json(text: str) -> list[SessionCost]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("ccusage session JSON: expected an object at top level, got %s" % type(data).__name__)
    rows = data.get("session") or data.get("sessions") or []
    if not isinstance(rows, list):
        raise ValueError("ccusage session JSON: 'session' is not a list")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sid = r.get("period") or r.get("sessionId")
        if not isinstance(sid, str) or not sid:
            continue
        out.append(SessionCost(sid, _coerce_float(r.get("totalCost")), _breakdown(r.get("modelBreakdowns"))))
    return out


def parse_daily_json(text: str) -> list[DailyCost]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("ccusage daily JSON: expected an object at top level, got %s" % type(data).__name__)
    rows = data.get("daily") or data.get("data") or []
    if not isinstance(rows, list):
        raise ValueError("ccusage daily JSON: 'daily' is not a list")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        day = r.get("period") or r.get("date")
        if not isinstance(day, str) or not day:
            continue
        cost = r.get("totalCost") if r.get("totalCost") is not None else r.get("costUSD")
        out.append(DailyCost(day[:10], _coerce_float(cost), _breakdown(r.get("modelBreakdowns"))))
    return out


def fetch_costs(command: list[str], runner: Runner = default_runner) -> CostResult:
    try:
        rc, out, err = runner(list(command) + ["session", "--json", "--offline"])
        if rc != 0:
            return CostResult(False, [], [], "ccusage session failed (%d): %s" % (rc, err.strip()[:300]))
        sessions = parse_session_json(out)
        rc, out, err = runner(list(command) + ["daily", "--json", "--offline"])
        if rc != 0:
            return CostResult(False, [], [], "ccusage daily failed (%d): %s" % (rc, err.strip()[:300]))
        daily = parse_daily_json(out)
    except Exception as e:  # any parsing/shape surprise -> unavailable, never raise
        return CostResult(False, [], [], "ccusage unavailable: %s" % e)
    return CostResult(True, sessions, daily, None)


def allocate(total: float, day_weights: list[tuple[str, float]]) -> dict[str, float]:
    if not day_weights:
        return {}
    weight_sum = sum(w for _, w in day_weights)
    if weight_sum <= 0:
        share = total / len(day_weights)
        return {d: share for d, _ in day_weights}
    return {d: total * w / weight_sum for d, w in day_weights}


def apply_costs(store: Store, result: CostResult) -> None:
    if not result.available:
        return
    store.set_session_costs([(s.session_id, s.cost, s.model_costs) for s in result.sessions])
    daily_rows: list[tuple[str, str, float]] = []
    for d in result.daily:
        daily_rows.append((d.day, "*", d.cost))
        daily_rows.extend((d.day, m, c) for m, c in d.model_costs.items())
    store.set_daily_costs(daily_rows)
    projects = {s["session_id"]: (s.get("project") or UNKNOWN_PROJECT) for s in store.sessions()}
    alloc_rows: list[tuple[str, str, str, float]] = []
    for s in result.sessions:
        if s.session_id not in projects:
            continue
        for day, cost in allocate(s.cost, store.session_days(s.session_id)).items():
            alloc_rows.append((day, projects[s.session_id], s.session_id, cost))
    store.set_allocated_costs(alloc_rows)


def blended_price(cost: float | None, total_tokens: int) -> float | None:
    if cost is None or not total_tokens:
        return None
    return cost / total_tokens
