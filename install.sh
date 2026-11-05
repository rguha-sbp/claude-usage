#!/bin/bash
# Installs the statusline, PreCompact hook and settings keys into
# $CLAUDE_CONFIG_DIR/settings.json, defaulting to ~/.claude/settings.json
# (spec Sec 4.9), then checks the optional prerequisites (terminal-notifier,
# ccusage) and prepares the state dir.
#
# No `set -e`: every step below is independent and reports its own OK/WARN so
# one missing prerequisite does not stop the rest from running. `set -u`
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

echo "== 1/5 Claude Code settings ($SETTINGS)"
SETTINGS_STATUS=0
(cd "$REPO" && python3 -m usage.install_settings "$SETTINGS" "$REPO") || SETTINGS_STATUS=$?
if [ "$SETTINGS_STATUS" -eq 0 ]; then
    echo "OK: statusline, PreCompact hook and cleanupPeriodDays merged."
else
    echo "FAIL: settings merge failed; fix this before relying on the statusline or notifications."
fi

echo "== 2/5 terminal-notifier"
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

echo "== 3/5 ccusage"
if VERSION="$(npx -y ccusage@20.0.48 --version 2>/dev/null)" && [ -n "$VERSION" ]; then
    echo "OK: ccusage $VERSION"
else
    echo "WARN: 'npx -y ccusage@20.0.48 --version' did not print a version."
    echo "      The dashboard will show tokens only, with dollar figures as 'unavailable', until this works."
fi

echo "== 4/5 state directory ($STATE_DIR)"
if mkdir -p "$STATE_DIR"; then
    echo "OK: $STATE_DIR ready."
else
    echo "WARN: could not create $STATE_DIR."
fi

echo "== 5/5 next steps"
echo "Start the dashboard:"
echo "  cd $REPO && python3 -m usage serve   (or: uv run usage serve)"
echo "  then open http://127.0.0.1:8765"
echo
echo "Start a new Claude Code session so the statusline picks up the new command."
echo
echo "The first notification asks macOS for permission. Trigger one now to grant it:"
echo "  echo '{\"session_id\":\"install-test\",\"session_name\":\"notification test\",\"context_window\":{\"context_window_size\":1000000,\"used_percentage\":19}}' | python3 $REPO/statusline/statusline.py"
echo "If nothing appears, open System Settings -> Notifications and allow terminal-notifier."

if [ "$SETTINGS_STATUS" -ne 0 ]; then
    exit 1
fi
exit 0
