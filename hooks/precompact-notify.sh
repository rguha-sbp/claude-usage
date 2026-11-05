#!/bin/bash
# Claude Code PreCompact hook (register with matcher "auto"; install.sh does this).
#
# Observes only: reads the PreCompact hook JSON on stdin, looks up the
# session title written by statusline/statusline.py, and fires a
# desktop notification before an automatic compaction runs.
#
# Hard rules, non-negotiable:
#   - MUST always exit 0, no matter what goes wrong.
#   - MUST never print anything to stdout (Claude Code shows PreCompact hook
#     stdout to the user).
#   - MUST NOT use `set -e` (a failing command here must not abort the hook).
#   - MUST return quickly: the notifier is launched fully detached in the
#     background so the hook does not block on it.
set -u

JQ=/usr/bin/jq
NOTIFIER="${USAGE_NOTIFIER:-/opt/homebrew/bin/terminal-notifier}"
# USAGE_STATE_DIR overrides everything (used by tests); otherwise this
# follows the same CLAUDE_CONFIG_DIR convention as usage/config.py and
# install.sh, so all three agree on where Claude Code's config lives.
STATE_DIR="${USAGE_STATE_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/usage-state}"

# Read stdin once; everything after this is best-effort.
INPUT="$(cat 2>/dev/null)"

# No jq, no notifier: nothing useful we can do. Exit quietly either way.
[ -x "$JQ" ] || exit 0
[ -x "$NOTIFIER" ] || exit 0

TRIGGER="$(printf '%s' "$INPUT" | "$JQ" -r '.trigger // empty' 2>/dev/null)"
# Defense in depth: even though this hook is registered with matcher "auto",
# only ever notify for an actual "auto" trigger.
[ "$TRIGGER" = "auto" ] || exit 0

SID="$(printf '%s' "$INPUT" | "$JQ" -r '.session_id // empty' 2>/dev/null)"
[ -n "$SID" ] || exit 0

# Sanitise session_id into a filename exactly like statusline.py's
# _safe_state_path: anything outside [A-Za-z0-9._-] becomes '_', truncated to
# 64 chars. Since '/' is replaced, this can never escape STATE_DIR.
SAFE_ID="$(printf '%s' "$SID" | tr -c 'A-Za-z0-9._-\n' '_' | cut -c1-64)"

TITLE=""
if [ -n "$SAFE_ID" ]; then
    STATE_FILE="$STATE_DIR/$SAFE_ID.json"
    if [ -r "$STATE_FILE" ]; then
        TITLE="$("$JQ" -r '.title // empty' "$STATE_FILE" 2>/dev/null)"
    fi
fi
[ -n "$TITLE" ] || TITLE="this session"

MSG="Auto-compact starting: $TITLE"

# Launch fully detached so the hook returns immediately: nohup insulates the
# notifier from SIGHUP when this script's process group exits, and the
# trailing '&' backgrounds it. Any failure here must not affect our exit code.
nohup "$NOTIFIER" -title "Claude Code" -message "$MSG" -group "$SID" >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
