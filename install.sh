#!/bin/bash
# Resolves a Python 3.15 interpreter, installs the statusline, PreCompact hook
# and settings keys into $CLAUDE_CONFIG_DIR/settings.json, defaulting to
# ~/.claude/settings.json (spec Sec 4.9), then checks the optional
# prerequisites (terminal-notifier, ccusage) and prepares the state dir.
#
# No `set -e`: every step after the interpreter is independent and reports
# its own OK/WARN so one missing prerequisite does not stop the rest from
# running. The interpreter is the exception: every Python step needs it, so
# without it the script stops at once. `set -u`
# catches unset-variable typos. REPO is resolved to an absolute path so the
# settings written by install_settings.py work from any working directory.
# CLAUDE_CONFIG_DIR, if set, overrides ~/.claude everywhere below (this
# matches the same env var usage/config.py already honours for the
# dashboard and statusline, so all three agree on where Claude Code's
# config lives).
set -u

REPO="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
NOTIFIER="/opt/homebrew/bin/terminal-notifier"
STATE_DIR="$CLAUDE_DIR/usage-state"

echo "== 1/6 Python 3.15 interpreter"
# The statusline command gets this absolute path, not `python3`: on macOS
# `python3` is the system 3.9. It is resolved once here so each prompt starts
# the interpreter directly, without the cost of `uv run`. `--system` skips
# virtual environments, so a repo .venv that may later be deleted is never
# chosen.
if ! command -v uv >/dev/null 2>&1; then
    echo "FAIL: uv not found. Install it (https://docs.astral.sh/uv/) and run ./install.sh again."
    exit 1
fi
PYTHON="$(uv python find --system 3.15 2>/dev/null)" \
    || { uv python install 3.15 && PYTHON="$(uv python find --system 3.15)"; } \
    || { echo "FAIL: could not find or install Python 3.15 with uv."; exit 1; }
echo "OK: $PYTHON"

echo "== 2/6 Claude Code settings ($SETTINGS)"
SETTINGS_STATUS=0
(cd "$REPO" && "$PYTHON" -m usage.install_settings "$SETTINGS" "$REPO" "$PYTHON") || SETTINGS_STATUS=$?
if [ "$SETTINGS_STATUS" -eq 0 ]; then
    echo "OK: statusline, PreCompact hook and cleanupPeriodDays merged."
else
    echo "FAIL: settings merge failed; fix this before relying on the statusline or notifications."
fi

echo "== 3/6 terminal-notifier"
if [ -x "$NOTIFIER" ]; then
    echo "OK: $NOTIFIER already installed."
elif command -v brew >/dev/null 2>&1; then
    echo "Installing terminal-notifier with Homebrew..."
    if brew install terminal-notifier; then
        echo "OK: terminal-notifier installed."
    else
        echo "WARN: 'brew install terminal-notifier' failed; notifications will not work until it is installed."
    fi
else
    echo "WARN: Homebrew not found and $NOTIFIER is missing."
    echo "      Install terminal-notifier manually: https://github.com/julienXX/terminal-notifier/releases"
fi

echo "== 4/6 ccusage"
if VERSION="$(npx -y ccusage@20.0.48 --version 2>/dev/null)" && [ -n "$VERSION" ]; then
    echo "OK: ccusage $VERSION"
else
    echo "WARN: 'npx -y ccusage@20.0.48 --version' did not print a version."
    echo "      The dashboard will show tokens only, with dollar figures as 'unavailable', until this works."
fi

echo "== 5/6 state directory ($STATE_DIR)"
if mkdir -p "$STATE_DIR"; then
    echo "OK: $STATE_DIR ready."
else
    echo "WARN: could not create $STATE_DIR."
fi

echo "== 6/6 next steps"
echo "Start the dashboard:"
echo "  cd $REPO && $PYTHON -m usage serve   (or: uv run usage serve)"
echo "  then open http://127.0.0.1:8765"
echo
echo "Start a new Claude Code session so the statusline picks up the new command."
echo
echo "The first notification asks macOS for permission. Trigger one now to grant it:"
echo "  echo '{\"session_id\":\"install-test\",\"session_name\":\"notification test\",\"context_window\":{\"context_window_size\":1000000,\"used_percentage\":19}}' | $PYTHON $REPO/statusline/statusline.py"
echo "If nothing appears, open System Settings -> Notifications and allow terminal-notifier."

if [ "$SETTINGS_STATUS" -ne 0 ]; then
    exit 1
fi
exit 0
