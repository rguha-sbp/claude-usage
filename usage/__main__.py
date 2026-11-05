"""CLI: `python3.15 -m usage serve|refresh|report` (spec §4.6)."""
import argparse
import logging
import sys
from datetime import date

from .config import load_config
from .pipeline import refresh
from .server import UsageServer
from .store import Store
from .summary import build_summary
from .tips import fmt_tokens, fmt_usd


def _cmd_refresh(store: Store, cfg) -> int:
    warnings = refresh(store, cfg)  # each warning is logged by the pipeline
    print("refresh complete: %d warning(s)" % len(warnings))
    return 0


def _cmd_report(store: Store, cfg, month) -> int:
    payload = build_summary(store, cfg, month, date.today())
    cost_available = payload["cost_available"]
    print("Usage report for %s%s" % (payload["month"], "" if cost_available else " (tokens; %s)" % payload["cost_error"]))
    print("-" * 72)
    sm = payload["summary"]
    if cost_available:
        print("to date: %s   projected: %s   pace/day: %s" % (fmt_usd(sm["to_date"]), fmt_usd(sm["projected"]), fmt_usd(sm["pace"])))
    else:
        print("to date: %s   projected: %s   pace/day: %s" % (fmt_tokens(sm["to_date"]), fmt_tokens(sm["projected"]), fmt_tokens(sm["pace"])))
    over_pct = ("%.0f%%" % (100 * sm["over_share"])) if sm["over_share"] is not None else "n/a"
    print("days elapsed: %d/%d   above %s threshold: %s" % (sm["days_elapsed"], sm["days_in_month"], fmt_tokens(payload["threshold"]), over_pct))
    print()
    header = "%-40s %10s %10s %5s" % ("session", ("cost" if cost_available else "tokens"), "peak", "tips")
    print(header)
    print("-" * len(header))
    top = sorted(payload["sessions"], key=(lambda s: s.get("cost") or 0.0) if cost_available else (lambda s: s["total_tokens"]),
                reverse=True)[:10]
    for s in top:
        value = fmt_usd(s["cost"]) if cost_available and s.get("cost") is not None else fmt_tokens(s["total_tokens"])
        print("%-40.40s %10s %10s %5d" % (s["display_title"], value, fmt_tokens(s["peak_context"]), len(s["tips"])))
    if payload["warnings"]:
        print("\n%d warning(s) from the last refresh." % len(payload["warnings"]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="usage", description="Claude Code usage dashboard")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also log every transcript file read (DEBUG); default logs one line per refresh")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="refresh in the background and serve the dashboard")
    s.add_argument("--port", type=int, default=None)

    sub.add_parser("refresh", help="parse transcripts and fetch costs once")

    r = sub.add_parser("report", help="print a plain-text usage report")
    r.add_argument("--month", default=None, help="YYYY-MM, defaults to the current/most recent month")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    cfg = load_config()
    store = Store(cfg.db_path)

    if args.cmd == "refresh":
        return _cmd_refresh(store, cfg)
    if args.cmd == "report":
        return _cmd_report(store, cfg, args.month)

    server = UsageServer(cfg, store, port=args.port)
    print("Usage dashboard on http://127.0.0.1:%d  (Ctrl-C to stop)" % server.port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
