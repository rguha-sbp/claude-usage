"""SQLite persistence (spec §4.2)."""
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .parser import FileState, SessionAgg, _safe_parse_ts

WEIGHTS = {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}

# Sentinel project name for calls/sessions with no derivable project (NULL
# `project` column). Shared with usage/cost.py so the `rollup` here and the
# `costs_allocated` writes there can never drift onto different sentinels.
UNKNOWN_PROJECT = "unknown"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL,
  offset INTEGER NOT NULL, last_call_ts TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, title TEXT, first_prompt TEXT, project TEXT, cwd TEXT,
  first_ts TEXT, last_ts TEXT,
  turns INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
  compactions INTEGER NOT NULL DEFAULT 0, tool_result_bytes INTEGER NOT NULL DEFAULT 0,
  content_bytes INTEGER NOT NULL DEFAULT 0, cold_restarts INTEGER NOT NULL DEFAULT 0,
  rebuild_tokens INTEGER NOT NULL DEFAULT 0, subagent_tokens INTEGER NOT NULL DEFAULT 0,
  repeated_reads TEXT NOT NULL DEFAULT '{}',
  calls INTEGER NOT NULL DEFAULT 0, models TEXT NOT NULL DEFAULT '{}',
  input_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  thinking_tokens INTEGER NOT NULL DEFAULT 0, peak_context INTEGER NOT NULL DEFAULT 0,
  calls_over_threshold INTEGER NOT NULL DEFAULT 0, tokens_over_threshold INTEGER NOT NULL DEFAULT 0,
  source_present INTEGER NOT NULL DEFAULT 1, updated_at TEXT);
CREATE TABLE IF NOT EXISTS calls(
  session_id TEXT NOT NULL, message_id TEXT NOT NULL, ts TEXT, day TEXT, hour INTEGER, model TEXT,
  input_tokens INTEGER, cache_write INTEGER, cache_read INTEGER, output INTEGER, thinking INTEGER,
  sidechain INTEGER NOT NULL DEFAULT 0, agent_id TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(session_id, message_id));
CREATE INDEX IF NOT EXISTS calls_day ON calls(day);
CREATE TABLE IF NOT EXISTS rollup(
  day TEXT NOT NULL, hour INTEGER, project TEXT NOT NULL, model TEXT NOT NULL,
  calls INTEGER, input_tokens INTEGER, cache_write_tokens INTEGER, cache_read_tokens INTEGER,
  output_tokens INTEGER, thinking_tokens INTEGER, calls_over_threshold INTEGER,
  tokens_over_threshold INTEGER, PRIMARY KEY(day, hour, project, model));
CREATE TABLE IF NOT EXISTS costs_session(
  session_id TEXT PRIMARY KEY, cost REAL, models TEXT NOT NULL DEFAULT '{}', fetched_at TEXT);
CREATE TABLE IF NOT EXISTS costs_daily(day TEXT NOT NULL, model TEXT NOT NULL, cost REAL, PRIMARY KEY(day, model));
CREATE TABLE IF NOT EXISTS costs_allocated(
  day TEXT NOT NULL, project TEXT NOT NULL, session_id TEXT NOT NULL, cost REAL, PRIMARY KEY(day, session_id));
CREATE TABLE IF NOT EXISTS skill_loads(
  session_id TEXT NOT NULL, load_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
  ts TEXT, day TEXT, sidechain INTEGER NOT NULL DEFAULT 0, body_bytes INTEGER,
  agent_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(session_id, load_id, name));
CREATE INDEX IF NOT EXISTS skill_loads_day ON skill_loads(day);
CREATE TABLE IF NOT EXISTS skill_bodies(name TEXT PRIMARY KEY, bytes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS compactions(
  session_id TEXT NOT NULL, agent_id TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL,
  PRIMARY KEY(session_id, agent_id, ts));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

# Injected text is sized in bytes (transcripts carry the text, not a token
# count); tokens are estimated at this ratio, which is what Anthropic quotes
# for English prose and code. The dashboard labels these figures as estimates.
BYTES_PER_TOKEN = 4

log = logging.getLogger(__name__)

_ADDITIVE = ("turns", "tool_calls", "compactions", "tool_result_bytes", "content_bytes",
             "cold_restarts", "rebuild_tokens", "subagent_tokens")

# Partial UNIQUE index enforcing global (cross-session) dedupe of non-sidechain
# message ids -- see Store._migrate/_dedupe_message_ids and Store.upsert_chunk.
# A sidechain (subagent) call keeps its own row even if its message_id
# collides with a main-thread call's id (see ADR/task in the fix report);
# this index therefore only constrains sidechain=0 rows.
CALLS_UNIQUE_MSG_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS calls_msgid_main ON calls(message_id) WHERE sidechain=0;"

# Bumped whenever Store.__init__ must run a one-time migration on open
# (see Store._migrate). Version "2" adds CALLS_UNIQUE_MSG_INDEX and the
# duplicate-resolution migration in _dedupe_message_ids. Version "3" adds
# calls.agent_id, the skills tables and compactions, and forces one full
# re-parse of every transcript so history gets skill rows (_force_reparse).
# Version "4" adds calls.hour (stamped for existing rows) and replaces the
# per-day `daily` rollup with the per-hour `rollup` (spec Sec 11).
SCHEMA_VERSION = "4"


def _local_dt(ts: Optional[str]) -> Optional[datetime]:
    """`ts` in the machine's local timezone, or None when `ts` is missing or
    not a well-formed timestamp. parse_records deliberately tolerates
    malformed timestamps on individual calls (spec Sec 6), so this must
    never raise -- a bad `ts` simply yields no day/hour bucket, and the
    caller (`Store.upsert_chunk`) still stores/counts the call."""
    if not ts:
        return None
    dt = _safe_parse_ts(ts)
    return dt.astimezone() if dt is not None else None


def local_day(ts: Optional[str]) -> Optional[str]:
    """'YYYY-MM-DD' in the machine's local timezone (see _local_dt)."""
    dt = _local_dt(ts)
    return dt.date().isoformat() if dt is not None else None


def local_hour(ts: Optional[str]) -> Optional[int]:
    """0-23 in the machine's local timezone (see _local_dt)."""
    dt = _local_dt(ts)
    return dt.hour if dt is not None else None


def _prefix(period: str) -> str:
    """LIKE pattern selecting the days of `period`, a month 'YYYY-MM' or a
    day 'YYYY-MM-DD'. `day` is always ten characters, so a month prefix
    matches exactly its days and a day prefix matches exactly that day."""
    return period + "%"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def weighted(input_tokens: int, cache_write: int, cache_read: int, output: int) -> float:
    return (input_tokens * WEIGHTS["input"] + cache_write * WEIGHTS["cache_write"]
            + cache_read * WEIGHTS["cache_read"] + output * WEIGHTS["output"])


class Store:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    # -- migration -----------------------------------------------------------
    def _migrate(self) -> None:
        """Idempotent. A store opened at an older schema_version runs the
        one-time duplicate-resolution pass below before the UNIQUE(message_id)
        WHERE sidechain=0 index can be created (SQLite refuses to add a
        UNIQUE index over rows that already violate it). A store already at
        SCHEMA_VERSION still gets the index recreated if somehow missing --
        CREATE INDEX IF NOT EXISTS makes that a cheap no-op in the common
        case."""
        version = self.meta_get("schema_version")
        if version != SCHEMA_VERSION:
            log.info("schema %s -> %s: forcing a full transcript re-parse on the next refresh",
                     version or "1", SCHEMA_VERSION)
            if version is None:
                self._dedupe_message_ids()  # pre-v2 store: resolve duplicates before the index
            self._ensure_column("calls", "agent_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("skill_loads", "agent_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("calls", "hour", "INTEGER")
            self._stamp_hours()
            self.conn.execute("DROP TABLE IF EXISTS daily")  # v3 per-day rollup, superseded by `rollup`
            self._force_reparse()
            self.meta_set("schema_version", SCHEMA_VERSION)
        self.conn.executescript(CALLS_UNIQUE_MSG_INDEX)
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(%s)" % table)}
        if column not in cols:
            self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))

    def _stamp_hours(self) -> None:
        """v4: give every kept call its local hour (a re-parse max-merges into
        existing main-thread rows, but a session whose transcript is gone
        would otherwise keep NULL hours forever)."""
        rows = self.conn.execute("SELECT rowid, ts FROM calls WHERE hour IS NULL AND ts IS NOT NULL").fetchall()
        with self.conn:
            self.conn.executemany("UPDATE calls SET hour=? WHERE rowid=?",
                                  [(local_hour(r["ts"]), r["rowid"]) for r in rows])

    def _force_reparse(self) -> None:
        """Make the next refresh read every transcript from offset 0 (v3
        migration): the skills tables are filled only while parsing, and
        sidechain calls need their agent_id. Main-thread calls are kept (a
        re-parse max-merges into them); sidechain calls, skill rows and
        compactions are rebuilt; the per-session additive counters are
        zeroed so the re-parse does not double them. Sessions whose
        transcript has since been deleted keep their token totals (derived
        from calls in recompute) but lose those counters."""
        with self.conn:
            self.conn.execute("DELETE FROM files")
            self.conn.execute("DELETE FROM calls WHERE sidechain=1")
            self.conn.execute("DELETE FROM skill_loads")
            self.conn.execute("DELETE FROM compactions")
            self.conn.execute("UPDATE sessions SET " + ", ".join("%s=0" % k for k in _ADDITIVE) +
                              ", repeated_reads='{}'")

    def _dedupe_message_ids(self) -> None:
        """Resolve pre-existing cross-session duplicate non-sidechain
        message_ids (the bug this fix addresses -- see fix report) so the
        UNIQUE(message_id) WHERE sidechain=0 index in _migrate can be
        created. For each duplicated message_id, keep the row belonging to
        the session with the earliest sessions.first_ts (ties broken by the
        lexicographically smaller session_id, matching the "first-seen"
        ruling applied going forward in upsert_chunk) and delete the rest.
        Idempotent: a store with no duplicate rows (including one already
        migrated) makes no changes."""
        dupes = self.conn.execute(
            "SELECT message_id FROM calls WHERE sidechain=0 "
            "GROUP BY message_id HAVING COUNT(DISTINCT session_id) > 1").fetchall()
        with self.conn:
            for d in dupes:
                mid = d["message_id"]
                rows = self.conn.execute(
                    "SELECT c.session_id AS session_id, s.first_ts AS first_ts FROM calls c "
                    "LEFT JOIN sessions s ON s.session_id = c.session_id "
                    "WHERE c.message_id=? AND c.sidechain=0", (mid,)).fetchall()
                winner = sorted(
                    rows, key=lambda r: (r["first_ts"] is None, r["first_ts"] or "", r["session_id"])
                )[0]["session_id"]
                for r in rows:
                    if r["session_id"] != winner:
                        self.conn.execute(
                            "DELETE FROM calls WHERE session_id=? AND message_id=? AND sidechain=0",
                            (r["session_id"], mid))

    # -- files -------------------------------------------------------------
    def file_states(self) -> Dict[str, FileState]:
        rows = self.conn.execute("SELECT * FROM files").fetchall()
        return {r["path"]: FileState(r["path"], r["size"], r["mtime"], r["offset"], r["last_call_ts"])
                for r in rows}

    def save_file_states(self, states: Dict[str, FileState]) -> None:
        # UPSERT-only: merges into existing rows, never replaces the table.
        # parse_incremental only returns states for files read this round,
        # so files not present in `states` must be left untouched.
        with self.conn:
            self.conn.executemany(
                "INSERT INTO files(path,size,mtime,offset,last_call_ts) VALUES(?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                "offset=excluded.offset, last_call_ts=excluded.last_call_ts",
                [(s.path, s.size, s.mtime, s.offset, s.last_call_ts) for s in states.values()])

    # -- sessions ----------------------------------------------------------
    def upsert_chunk(self, agg: SessionAgg) -> None:
        row = self.conn.execute("SELECT * FROM sessions WHERE session_id=?", (agg.session_id,)).fetchone()
        with self.conn:
            if row is None:
                self.conn.execute(
                    "INSERT INTO sessions(session_id,title,first_prompt,project,cwd,first_ts,last_ts,"
                    "turns,tool_calls,compactions,tool_result_bytes,content_bytes,cold_restarts,"
                    "rebuild_tokens,subagent_tokens,repeated_reads,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (agg.session_id, agg.title, agg.first_prompt, agg.project, agg.cwd, agg.first_ts,
                     agg.last_ts, agg.turns, agg.tool_calls, agg.compactions, agg.tool_result_bytes,
                     agg.content_bytes, agg.cold_restarts, agg.rebuild_tokens, agg.subagent_tokens,
                     json.dumps(agg.read_counts), _now()))
            else:
                reads = json.loads(row["repeated_reads"] or "{}")
                for k, v in agg.read_counts.items():
                    reads[k] = reads.get(k, 0) + v
                first_ts = min(x for x in (row["first_ts"], agg.first_ts) if x) if (row["first_ts"] or agg.first_ts) else None
                last_ts = max(x for x in (row["last_ts"], agg.last_ts) if x) if (row["last_ts"] or agg.last_ts) else None
                values = [row[k] + getattr(agg, k) for k in _ADDITIVE]
                # SessionAgg.merge_from semantics: title/first_prompt/project/cwd
                # are kept if already set on the existing row; only filled in
                # from the new chunk when the existing value is NULL. This is
                # COALESCE(existing, new), not COALESCE(new, existing).
                self.conn.execute(
                    "UPDATE sessions SET title=COALESCE(title,?), first_prompt=COALESCE(first_prompt,?), "
                    "project=COALESCE(project,?), cwd=COALESCE(cwd,?), first_ts=?, last_ts=?, "
                    + ", ".join("%s=?" % k for k in _ADDITIVE) +
                    ", repeated_reads=?, updated_at=? WHERE session_id=?",
                    [agg.title, agg.first_prompt, agg.project, agg.cwd, first_ts, last_ts] + values
                    + [json.dumps(reads), _now(), agg.session_id])
            # A call with a missing/unparsable `ts` still gets a `day` of
            # None (local_day never raises -- see its docstring); it is
            # still stored and counted in session totals/threshold math,
            # just excluded from the day/hour-bucketed `rollup`.
            #
            # Two ON CONFLICT clauses target the table's two constraints:
            #   - PRIMARY KEY(session_id, message_id): the same session
            #     re-reporting the same call (e.g. a later streaming
            #     snapshot) -- keep the existing max-merge behaviour.
            #   - CALLS_UNIQUE_MSG_INDEX, UNIQUE(message_id) WHERE
            #     sidechain=0: a DIFFERENT session reporting a non-sidechain
            #     message_id already claimed by another session -- this is
            #     the resumed/forked-session-copies-history case (see fix
            #     report); DO NOTHING so the call is attributed to exactly
            #     one session, globally, like ccusage. Whichever session's
            #     chunk is upserted first wins (see pipeline.refresh, which
            #     orders chunks by first_ts so that's the original session,
            #     not whichever transcript file happened to be read first).
            #     A sidechain call is never constrained by this index, so a
            #     sidechain id colliding with a main-thread id in another
            #     session still inserts normally (kept, per the ruling).
            self.conn.executemany(
                "INSERT INTO calls(session_id,message_id,ts,day,hour,model,input_tokens,cache_write,cache_read,"
                "output,thinking,sidechain,agent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id,message_id) DO UPDATE SET day=excluded.day, hour=excluded.hour, "
                "input_tokens=max(input_tokens,excluded.input_tokens), cache_write=max(cache_write,excluded.cache_write), "
                "cache_read=max(cache_read,excluded.cache_read), output=max(output,excluded.output), "
                "thinking=max(thinking,excluded.thinking), agent_id=excluded.agent_id "
                "ON CONFLICT(message_id) WHERE sidechain=0 DO NOTHING",
                [(agg.session_id, c.message_id, c.ts, local_day(c.ts), local_hour(c.ts), c.model,
                  c.input_tokens, c.cache_write, c.cache_read, c.output, c.thinking, int(c.sidechain), c.agent_id)
                 for c in agg.calls])
            # Skill loads and injected attachments (see parser.SkillLoad). A
            # re-read chunk re-reports the same rows; body_bytes keeps the
            # known value (NULL means "not carried by this record", never 0).
            self.conn.executemany(
                "INSERT INTO skill_loads(session_id,load_id,name,kind,ts,day,sidechain,body_bytes,agent_id) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,load_id,name) DO UPDATE SET "
                "body_bytes=CASE WHEN body_bytes IS NULL THEN excluded.body_bytes "
                "WHEN excluded.body_bytes IS NULL THEN body_bytes ELSE max(body_bytes,excluded.body_bytes) END",
                [(agg.session_id, l.load_id, l.name, l.kind, l.ts, local_day(l.ts), int(l.sidechain), l.body_bytes,
                  l.agent_id) for l in agg.skill_loads])
            self.conn.executemany(
                "INSERT OR IGNORE INTO compactions(session_id,agent_id,ts) VALUES(?,?,?)",
                [(agg.session_id, aid, ts) for aid, ts in agg.compactions_at if ts])
            # A skill's body is the same text wherever it is loaded, so one
            # session that carries the `invoked_skills` attachment sizes that
            # skill exactly for every session, including older transcripts.
            self.conn.executemany(
                "INSERT INTO skill_bodies(name,bytes) VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET bytes=max(bytes,excluded.bytes)",
                list(agg.skill_bodies.items()))

    def mark_sources(self, present_paths: Set[str]) -> None:
        with self.conn:
            ids = [r["session_id"] for r in self.conn.execute("SELECT session_id FROM sessions")]
            for sid in ids:
                present = any(p.endswith("/%s.jsonl" % sid) for p in present_paths)
                self.conn.execute("UPDATE sessions SET source_present=? WHERE session_id=?", (int(present), sid))

    def recompute(self, threshold: int) -> None:
        with self.conn:
            for r in self.conn.execute("SELECT session_id FROM sessions").fetchall():
                sid = r["session_id"]
                agg = self.conn.execute(
                    "SELECT COUNT(*) n, COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(cache_write),0) cw, "
                    "COALESCE(SUM(cache_read),0) cr, COALESCE(SUM(output),0) o, COALESCE(SUM(thinking),0) th "
                    "FROM calls WHERE session_id=?", (sid,)).fetchone()
                # calls_over_threshold, tokens_over_threshold, and peak_context are
                # computed over non-sidechain calls only; subagent tokens are still
                # included in the totals above via input_tokens/cache_*/output sums.
                main = self.conn.execute(
                    "SELECT COALESCE(MAX(input_tokens+cache_write+cache_read),0) peak, "
                    "COALESCE(SUM(CASE WHEN input_tokens+cache_write+cache_read>? THEN 1 ELSE 0 END),0) n_over, "
                    "COALESCE(SUM(CASE WHEN input_tokens+cache_write+cache_read>? THEN input_tokens+cache_write+cache_read-? ELSE 0 END),0) t_over "
                    "FROM calls WHERE session_id=? AND sidechain=0", (threshold, threshold, threshold, sid)).fetchone()
                models = {m["model"]: m["n"] for m in self.conn.execute(
                    "SELECT model, COUNT(*) n FROM calls WHERE session_id=? GROUP BY model", (sid,))}
                self.conn.execute(
                    "UPDATE sessions SET calls=?, models=?, input_tokens=?, cache_write_tokens=?, cache_read_tokens=?, "
                    "output_tokens=?, thinking_tokens=?, peak_context=?, calls_over_threshold=?, tokens_over_threshold=? "
                    "WHERE session_id=?",
                    (agg["n"], json.dumps(models), agg["i"], agg["cw"], agg["cr"], agg["o"], agg["th"],
                     main["peak"], main["n_over"], main["t_over"], sid))
            self.conn.execute("DELETE FROM rollup")
            self.conn.execute(
                ("INSERT INTO rollup SELECT c.day, c.hour, COALESCE(s.project,'%(u)s'), c.model, COUNT(*), "
                 "SUM(c.input_tokens), SUM(c.cache_write), SUM(c.cache_read), SUM(c.output), SUM(c.thinking), "
                 "SUM(CASE WHEN c.sidechain=0 AND c.input_tokens+c.cache_write+c.cache_read>? THEN 1 ELSE 0 END), "
                 "SUM(CASE WHEN c.sidechain=0 AND c.input_tokens+c.cache_write+c.cache_read>? "
                 "THEN c.input_tokens+c.cache_write+c.cache_read-? ELSE 0 END) "
                 "FROM calls c JOIN sessions s ON s.session_id=c.session_id WHERE c.day IS NOT NULL "
                 "GROUP BY c.day, c.hour, COALESCE(s.project,'%(u)s'), c.model") % {"u": UNKNOWN_PROJECT},
                (threshold, threshold, threshold))

    # -- costs -------------------------------------------------------------
    def set_session_costs(self, rows: List[Tuple[str, float, Dict[str, float]]]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO costs_session(session_id,cost,models,fetched_at) VALUES(?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET cost=excluded.cost, models=excluded.models, fetched_at=excluded.fetched_at",
                [(sid, cost, json.dumps(models), _now()) for sid, cost, models in rows])

    def set_daily_costs(self, rows: List[Tuple[str, str, float]]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO costs_daily(day,model,cost) VALUES(?,?,?) "
                "ON CONFLICT(day,model) DO UPDATE SET cost=excluded.cost", rows)

    def set_allocated_costs(self, rows: List[Tuple[str, str, str, float]]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO costs_allocated(day,project,session_id,cost) VALUES(?,?,?,?) "
                "ON CONFLICT(day,session_id) DO UPDATE SET project=excluded.project, cost=excluded.cost", rows)

    # -- queries -----------------------------------------------------------
    def _session_dict(self, r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        d["models"] = json.loads(d.get("models") or "{}")
        d["repeated_reads"] = json.loads(d.get("repeated_reads") or "{}")
        d["model_costs"] = json.loads(d.pop("cost_models", None) or "{}")
        d["source_present"] = bool(d["source_present"])
        d["total_tokens"] = d["input_tokens"] + d["cache_write_tokens"] + d["cache_read_tokens"] + d["output_tokens"]
        return d

    _SESSION_SQL = ("SELECT s.*, cs.cost AS cost, cs.models AS cost_models FROM sessions s "
                    "LEFT JOIN costs_session cs ON cs.session_id=s.session_id ")

    def sessions(self, period: Optional[str] = None) -> List[Dict[str, Any]]:
        """Sessions with at least one call in `period` (see _prefix), newest
        first; every session when `period` is None."""
        if period is None:
            rows = self.conn.execute(self._SESSION_SQL + "ORDER BY s.last_ts DESC").fetchall()
        else:
            rows = self.conn.execute(
                self._SESSION_SQL + "WHERE s.session_id IN (SELECT DISTINCT session_id FROM calls WHERE day LIKE ?) "
                "ORDER BY s.last_ts DESC", (_prefix(period),)).fetchall()
        return [self._session_dict(r) for r in rows]

    def session(self, session_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute(self._SESSION_SQL + "WHERE s.session_id=?", (session_id,)).fetchone()
        return self._session_dict(r) if r else None

    def session_days(self, session_id: str) -> List[Tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT day, SUM(input_tokens) i, SUM(cache_write) cw, SUM(cache_read) cr, SUM(output) o "
            "FROM calls WHERE session_id=? AND day IS NOT NULL GROUP BY day ORDER BY day", (session_id,)).fetchall()
        return [(r["day"], weighted(r["i"], r["cw"], r["cr"], r["o"])) for r in rows]

    def rollup(self, period: str) -> List[Dict[str, Any]]:
        """Per (day, hour, project, model) usage rows in `period`, ordered by
        day then hour. Rebuilt by recompute."""
        rows = self.conn.execute(
            "SELECT * FROM rollup WHERE day LIKE ? ORDER BY day, hour", (_prefix(period),)).fetchall()
        return [dict(r) for r in rows]

    def daily_costs(self, period: str) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for r in self.conn.execute("SELECT day, model, cost FROM costs_daily WHERE day LIKE ?", (_prefix(period),)):
            out.setdefault(r["day"], {})[r["model"]] = r["cost"]
        return out

    def allocated_by_project(self, period: str) -> Dict[str, float]:
        rows = self.conn.execute(
            "SELECT project, SUM(cost) c FROM costs_allocated WHERE day LIKE ? GROUP BY project", (_prefix(period),))
        return {r["project"]: r["c"] for r in rows}

    def months_with_data(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT substr(day,1,7) m FROM calls WHERE day IS NOT NULL ORDER BY m").fetchall()
        return [r["m"] for r in rows]

    def days_with_data(self, month: str) -> List[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT day FROM calls WHERE day LIKE ? ORDER BY day", (_prefix(month),)).fetchall()
        return [r["day"] for r in rows]

    # -- skills ------------------------------------------------------------
    def skill_usage(self, period: str) -> List[Dict[str, Any]]:
        """One row per skill load / injected attachment whose load day falls
        in `period` (see _prefix): session_id, name, kind, ts,
        injected_tokens, exact, calls_carried.

        injected_tokens is exact (body bytes / BYTES_PER_TOKEN) when the
        text itself was seen -- on the row for hooks and the skill listing,
        or in skill_bodies for a skill loaded with the `invoked_skills`
        attachment in any session. Otherwise it is estimated as the context
        growth on the first call after the load, minus the loading call's
        own output (which also becomes input on the next call); clamped at
        0 (a compaction in between makes the raw delta negative) and None
        when no later call exists yet. calls_carried is how many later
        calls of the same transcript (session + agent_id) then carried that
        text, up to that transcript's next compaction, which drops it."""
        rows = self.conn.execute(
            "SELECT l.session_id, l.load_id, l.name, l.kind, l.ts, l.agent_id, l.body_bytes, b.bytes AS known_bytes "
            "FROM skill_loads l LEFT JOIN skill_bodies b ON b.name=l.name "
            "WHERE l.day LIKE ? ORDER BY l.ts, l.session_id, l.name", (_prefix(period),)).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            size = r["body_bytes"] if r["body_bytes"] is not None else r["known_bytes"]
            exact = size is not None
            if exact:
                tokens: Optional[int] = size // BYTES_PER_TOKEN
            else:
                tokens = None
                load = self.conn.execute(
                    "SELECT input_tokens+cache_write+cache_read AS ctx, output FROM calls WHERE message_id=? "
                    "ORDER BY session_id=? DESC LIMIT 1", (r["load_id"], r["session_id"])).fetchone()
                nxt = self.conn.execute(
                    "SELECT input_tokens+cache_write+cache_read AS ctx FROM calls "
                    "WHERE session_id=? AND agent_id=? AND ts>? ORDER BY ts LIMIT 1",
                    (r["session_id"], r["agent_id"], r["ts"])).fetchone()
                if load is not None and nxt is not None:
                    tokens = max(0, nxt["ctx"] - (load["ctx"] + load["output"]))
            carried = self.conn.execute(
                "SELECT COUNT(*) n FROM calls WHERE session_id=? AND agent_id=? AND ts>? AND ts<COALESCE("
                "(SELECT MIN(ts) FROM compactions WHERE session_id=? AND agent_id=? AND ts>?), '~')",
                (r["session_id"], r["agent_id"], r["ts"], r["session_id"], r["agent_id"], r["ts"])).fetchone()["n"]
            out.append({"session_id": r["session_id"], "name": r["name"], "kind": r["kind"], "ts": r["ts"],
                        "injected_tokens": tokens, "exact": exact, "calls_carried": carried})
        return out

    # -- meta --------------------------------------------------------------
    def meta_get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))
        self.conn.commit()
