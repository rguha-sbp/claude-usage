"""One refresh cycle: parse -> store -> recompute -> costs (spec §4.6)."""
import logging
import time
from datetime import datetime, timezone

from .config import Config
from .cost import Runner, apply_costs, default_runner, fetch_costs
from .parser import parse_incremental
from .store import Store

log = logging.getLogger(__name__)


def refresh(store: Store, cfg: Config, runner: Runner = default_runner) -> list[str]:
    """Parse new/changed transcripts since the last round, persist them,
    recompute derived session/day rollups, then fetch costs from ccusage
    every time (never cached) and apply them. Costs unavailable this round
    leaves previously-stored cost rows untouched; `cost_available` reflects
    only this round's fetch."""
    started = time.monotonic()
    warnings: list[str] = []
    states = store.file_states()
    result = parse_incremental(cfg.claude_dir, states)
    # Explicit, deterministic order: earliest session first_ts first. This
    # is what a resumed/forked session (a new session id whose transcript
    # is a copy of an earlier session's history plus new records -- see
    # fix report) needs: Store.upsert_chunk's UNIQUE(message_id) WHERE
    # sidechain=0 dedupe attributes each shared call to whichever chunk is
    # upserted first, so the original (earlier) session must always be
    # upserted before the fork. A copied-history fork's first record often
    # shares the *same* timestamp as the original's (an exact tie here);
    # Python's sort is stable, so such ties fall back to result.chunks'
    # existing order, which parse_incremental already produced by file
    # mtime (see _discovery_sort_key) -- the fork's transcript file is
    # always created on disk after the original's.
    ordered = sorted(result.chunks.values(), key=lambda agg: agg.first_ts or "9999-99-99T00:00:00Z")
    for agg in ordered:
        store.upsert_chunk(agg)
    store.save_file_states(result.file_states)
    store.mark_sources(result.present_paths)
    store.recompute(cfg.threshold_tokens)
    warnings.extend(result.warnings)

    costs = fetch_costs(cfg.ccusage_command, runner)
    apply_costs(store, costs)
    store.meta_set("cost_available", "1" if costs.available else "0")
    store.meta_set("cost_error", costs.error or "")
    if not costs.available and costs.error:
        warnings.append(costs.error)
    store.meta_set("generated_at", datetime.now(timezone.utc).isoformat())
    for w in warnings:
        log.warning(w)
    log.info("refresh: %d file(s) read, %d session(s) updated, %d call(s), %d skill load(s); costs %s; %.1fs",
             len(result.file_states), len(ordered), sum(len(a.calls) for a in ordered),
             sum(len(a.skill_loads) for a in ordered), "available" if costs.available else "unavailable",
             time.monotonic() - started)
    return warnings
