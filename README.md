# Claude Code usage dashboard

A local dashboard, statusline and macOS notifications that show where Claude
Code's context and dollars go, and warn before a session runs into an
automatic compaction. Everything runs on this machine against files Claude
Code already writes to `~/.claude`; there is no server component and no
account to sign into.

It costs nothing to run: the dashboard makes no API calls to Anthropic or
anyone else. It parses local JSONL transcripts and, for dollar figures only,
shells out to `npx ccusage`, a local tool that reads the same transcripts. If
`ccusage` cannot run, the dashboard still works and shows token counts
instead of dollars; nothing is estimated or guessed.

## Requirements

- Python 3.9 or later. There are no Python dependencies.
- Claude Code, which writes the transcripts under `~/.claude`.
- Optional: Node, so that `npx ccusage` can produce dollar figures.
- Optional, macOS only: `terminal-notifier` and `jq` for the desktop
  notifications. The dashboard and statusline also work on Linux.

The notifier path defaults to `/opt/homebrew/bin/terminal-notifier`
(Homebrew on Apple Silicon). On an Intel Mac, set `notifier` in
`config.json` to `/usr/local/bin/terminal-notifier`.

## Quick start

With [uv](https://docs.astral.sh/uv/) (no separate install step needed):

    uv run usage serve

Then open <http://127.0.0.1:8765>. The page refreshes itself every 60
seconds; `uv run usage refresh` runs one parse-and-cost pass on demand, and
`uv run usage report` prints a plain-text table of the current month's
sessions to the terminal.

Without uv, plain Python 3.9+ works the same way, no dependencies to
install:

    python3 -m usage serve
    python3 -m usage refresh
    python3 -m usage report

## What the four tabs show

Every tab is scoped to a **period**: a whole month, or a single day. The
two pickers in the header choose the month and then, optionally, a day
of that month with data ("Whole month" goes back). Clicking a bar in the
month chart opens that day. The period lives in the URL (`?period=2026-09`
or `?period=2026-09-14`), so a day view can be bookmarked and reloaded.

- **Month** — the period's total tokens and dollars to date. For a month:
  a projected total, a daily bar chart (actual bars plus a faded
  projection for the rest of the month) and a comparison with the previous
  month. For a day: 24 hourly bars, no projection, and a comparison with
  the previous day (the day's ccusage cost is split across its hours by
  weighted tokens). Then how much of the period's spend has
  been above the configured threshold, a model split and a token-type
  split (input / cache write / cache read / output), and a **Skills and
  injected context** table: every skill loaded with the Skill tool, every
  SessionStart hook's injected context and the skill listing menu, with
  how many times each was loaded, its size in tokens, and how many tokens
  it cost once re-read on every later call of the session ("carried").
- **Sessions** — every session active in the period as a row (title, working
  directory, cost or tokens, peak context reached, skills loaded),
  sorted by spend; click a row for a detail panel with cost/token
  breakdowns, the skills it carried, and the tips that fired for it.
- **Projects** — the same figures rolled up by project directory, useful
  for seeing which repo or client is actually costing the most.
- **Office** — every Claude Code session that is running right now as a
  small worker at a desk, in an isometric campus: one room per project,
  each with a water cooler, a plant and a doorway, and out in front a
  plaza with a canteen, a notice board and a server room. A worker types
  while its session is busy (or waits on a foreground sub-agent), leaning
  back to think now and then, and slumps over the desk with z's once the
  session has been quiet for a minute. Sleepers stay asleep at their desks.
  A working lead sometimes takes a short break between thoughts: a drink
  at the cooler, a chat in the aisle with another worker from the same
  room, or a walk out to the canteen — the one place where workers from
  different projects stand together and talk. A session that falls asleep
  mid-break walks home and dozes. When the session's process exits the
  worker walks out through the door and fades. Each worker's hair, glasses and coat come from its session id and
  its shirt from its model, so the same session always looks the same.
  The model decides the rest (only leads, never sub-agent interns, get the
  crown and the halo): Opus wears a crown and keeps a trophy on a
  gold-trimmed desk, Fable a monocle and a silk coat beside an orchid,
  Sonnet is an ordinary worker and Haiku is smaller, quicker and wears
  headphones (`dashboard/office-cast.js`). The notice board carries the
  day's real numbers — spend so far, the busiest project, how many
  sessions are awake — and the racks in the server room blink faster while
  sessions are working. The whole scene is lit by your own clock: morning,
  afternoon, sunset, then dark windows, stars and desk lamps after hours.
  The behaviour is a small state machine per worker with steering along
  waypoints (`dashboard/office-sim.js`), the campus layout and scenery are
  pure functions (`dashboard/office-world.js`), and `dashboard/office.js`
  is the controller that fetches and animates. Sub-agents show up as interns
  beside their lead (tethered to whoever spawned them, with a glyph for the
  tool they are using), and the Agents rail lists each one with its type,
  task, model, tokens, tool count and current tool; finished ones stay
  greyed for two hours. None of the first three
  touch the DOM, so node drives them in `tests/office_*_check.js`. Hover
  for the title, model and how long it has been in that state, plus the
  last thing the session said when it is asleep (usually the question it
  is waiting on). Click to open the session on the Sessions tab. State
  comes from the statusline alone: every run stamps the session's file
  under `~/.claude/usage-state/` with the time, working directory, model,
  transcript path and the Claude process id. No hooks, and nothing in
  the office ever acts on a session. Thresholds live in `config.json`
  under `office` (`working_seconds`, default 60; `gone_after_seconds`,
  default 86400, used only when no process id is known).

When `ccusage` cannot produce a dollar figure, every cost cell reads
"unavailable" and the page falls back to showing tokens; it never invents a
number.

Skill and hook sizes come from the transcript. Recent Claude Code versions
record the injected text itself (an `invoked_skills` attachment for skills,
`hook_additional_context` and `skill_listing` attachments for the rest);
those are sized at 4 bytes per token and shown as exact. A skill whose text
was never recorded is estimated from how much the context grew on the call
after the load and is marked "est.". Carried tokens count that text on
every later call of the same transcript, up to its next compaction, which
drops it. A skill's body is the same wherever it is loaded, so one session
that recorded it sizes that skill exactly for every other session too.

## Thresholds and how to change them

Thresholds live in `config.json` at the repo root:

```json
{
  "threshold_tokens": 200000,
  "statusline": { "warn_tokens": 150000, "alert_tokens": 180000 }
}
```

- `threshold_tokens` (default 200000) — the dashboard's "spend above this
  many tokens counts as over-threshold" line, used for the daily-bar
  shading and the month summary's "above threshold" percentage.
- `statusline.warn_tokens` (default 150000) — the statusline segment turns
  yellow and, the first time a session crosses it, fires the "wrap up and
  /compact" notification below.
- `statusline.alert_tokens` (default 180000) — the statusline segment turns
  red and fires the "compact now" notification.

Edit `config.json` and the change applies on the next refresh (dashboard) or
the next statusline invocation; nothing needs to be reparsed or restarted.

## Install the statusline and alerts

    ./install.sh

This is the only part of the project that touches anything outside this
repo. It runs in five steps, each reporting OK or WARN so one missing
prerequisite does not stop the rest:

1. Runs `python3 -m usage.install_settings` against
   `~/.claude/settings.json`. `CLAUDE_CONFIG_DIR`, when set, overrides
   `config.json`'s `claude_dir` for every component (installer, hook,
   statusline and dashboard), so if it is set this step writes
   `$CLAUDE_CONFIG_DIR/settings.json` instead. This step:
   - backs up the existing file first, to
     `~/.claude/settings.json.bak-YYYYMMDD-HHMMSS`;
   - sets `statusLine` to run `statusline/statusline.py` from this repo;
   - adds a `PreCompact` hook (matcher `auto`) that runs
     `hooks/precompact-notify.sh`, replacing any earlier registration of
     that same hook script and leaving every other hook and settings key
     (model, plugins, theme, and so on) untouched;
   - raises `cleanupPeriodDays` to at least 90 (never lowers it if it was
     already higher);
   - removes `autoCompactWindow` if it is exactly 200000, the value an
     earlier version of this installer wrote. This tool alerts, it never
     decides when Claude Code compacts; a different value you set yourself
     is left alone.
   If the existing settings file is not valid JSON, this step fails,
   prints one line explaining why, and does not touch the file; every
   later step still runs, but `install.sh` exits 1 at the end.
2. Installs `terminal-notifier` with Homebrew if it is not already at
   `/opt/homebrew/bin/terminal-notifier` (prints manual install
   instructions if Homebrew itself is missing).
3. Checks that `npx -y ccusage@20.0.48 --version` prints a version; if not,
   this is a warning, not a failure — the dashboard still works, showing
   tokens instead of dollars.
4. Creates `~/.claude/usage-state/`, where the statusline keeps its
   per-session notification state.
5. Prints the start command, the dashboard URL, and a one-line reminder
   about the one-time notification permission (see below).

Start a new Claude Code session afterwards so it picks up the new
statusline command.

### One-time notification permission

The first time `terminal-notifier` fires, macOS asks whether to allow
notifications from it. `install.sh` triggers one automatically so you get
that prompt right away; if you miss it or nothing appears, open **System
Settings → Notifications**, find `terminal-notifier`, and allow it. You can
also test manually with a fake statusline payload above the warn threshold:

    echo '{"session_id":"test","session_name":"notification test","context_window":{"context_window_size":1000000,"used_percentage":19}}' \
      | python3 statusline/statusline.py

## What the notifications say, and when

Both come from `~/.claude/usage-state/<session_id>.json`, which tracks
whether a session has already been warned so each threshold only notifies
once per crossing (it resets if the context drops back below `warn`,
meaning a compaction happened):

- **150,000 tokens (warn)** — "Context 150K in \<title\>: wrap up and
  /compact". The statusline segment turns yellow at the same point.
- **180,000 tokens (alert)** — "Context 180K in \<title\>: compact now".
  The statusline segment turns red.
- **Whenever Claude Code compacts on its own** — the `PreCompact` hook
  fires first and notifies "Auto-compact starting: \<title\>" so you know
  it is about to happen. Nothing here changes when that is; auto-compact
  stays whatever you set in Claude Code (`/config`).

## Run at login (optional)

Not installed by default. To have the dashboard start automatically, save
the following as `~/Library/LaunchAgents/com.example.claude-usage.plist`,
replacing `/path/to/repo` with this repo's absolute path, `/path/to/uv` with
the output of `command -v uv`, and `YOU` with your user name:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.example.claude-usage</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/uv</string>
        <string>run</string>
        <string>--directory</string>
        <string>/path/to/repo</string>
        <string>usage</string>
        <string>serve</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/repo</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/Users/YOU/.local/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/claude-usage.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-usage.err</string>
</dict>
</plist>
```

Then load or unload it with:

    launchctl load ~/Library/LaunchAgents/com.example.claude-usage.plist
    launchctl unload ~/Library/LaunchAgents/com.example.claude-usage.plist

`RunAtLoad` starts it at login (and immediately when you `load` it);
`KeepAlive` restarts it if it ever exits. `EnvironmentVariables.PATH`
includes `/opt/homebrew/bin` and `/usr/local/bin` so `npx`/`node` are found
by ccusage when launchd starts this with its own minimal environment
(launchd does not inherit your shell's `PATH`); without it, costs would
show "unavailable" even though the same command works fine from a
terminal. `WorkingDirectory` matches `--directory` above for the same
reason: launchd's default working directory is `/`, not this repo.

## Logs

Every command logs to stderr: one line per refresh (files read, sessions
updated, calls, skill loads, whether ccusage produced dollars, elapsed
time), one WARNING per skipped transcript line or ccusage failure, and the
one-time schema migration when it runs. A background refresh that fails
logs the traceback and retries at the next interval instead of stopping.
Add `-v` for a DEBUG line per transcript file read (path, byte offset,
bytes, calls):

    uv run usage -v serve

## Troubleshooting

- **No notifications appear** — most likely macOS permissions or Focus
  mode. Check System Settings → Notifications → terminal-notifier is
  allowed, and that Focus/Do Not Disturb is not silencing it. Re-run the
  manual test command above.
- **Costs show "unavailable"** — `ccusage` could not run. Check that
  `npx -y ccusage@20.0.48 --version` works from a terminal (needs Node);
  check the server logs for the last error. Tokens are always shown even
  when dollars are not.
- **The dashboard looks stale** — the background refresh runs every 60
  seconds (`config.json`'s `refresh_seconds`); the page shows a "stale
  since" note if a refresh has been failing. Restarting `usage serve`
  forces an immediate refresh.

## How it works

`usage/parser.py` incrementally reads Claude Code's JSONL transcripts under
`~/.claude/projects/`, tracking a byte offset per file so repeat parses are
cheap; `usage/store.py` keeps the parsed records in a local SQLite database
(`usage.db`) so the server never re-parses on every request. The store
carries a schema version and migrates itself on open; a version step
(version 3 added the skills tables and per-transcript call attribution,
version 4 the local hour on every call and the per-hour rollup behind the
day view) resets every file offset so the next refresh re-reads all
transcripts once, which takes a few seconds. `usage/projection.py` turns
the rollup into the period's totals, trend and chart series.
`usage/skills.py` folds the per-load rows into the skills table. `usage/cost.py`
joins those records against `npx ccusage`'s JSON output to attach dollar
figures. `usage/server.py` serves the dashboard's static assets and a
`/api/summary` endpoint from a background refresh thread, plus `/api/office`
(usage/office.py), which reads the statusline's state files and no database; the
handler joins on the notice board's numbers for today (`board_stats` in
usage/summary.py), the one part of that payload that comes from the
database. `/api/summary` takes
`?period=YYYY-MM` or `?period=YYYY-MM-DD` (`?month=` still works) and
answers 400 for anything else. The server listens on 127.0.0.1 only and
answers 403 to any request whose `Host` header is not `127.0.0.1:<port>`
or `localhost:<port>`, so a web page that rebinds its own DNS name to
127.0.0.1 cannot read your session titles and messages. The page is fluid
up to 1680px wide.
`statusline/statusline.py` is invoked by Claude Code itself after every
assistant message, reading the same session's live context size from stdin;
it never parses transcripts or calls the network, so it stays fast.
`hooks/precompact-notify.sh` is the one piece that runs as a Claude Code
hook rather than being invoked by this project.

## Design

`docs/design.md` is the design document. Code comments such as
"spec Sec 12" refer to its sections.

## Tests

    python3 -m pytest -q

or, with uv (also installs pytest into an isolated environment):

    uv run --group dev pytest -q

The node checks for the Office (`tests/office_*_check.js`) run as part of
pytest when `node` is on the PATH. GitHub Actions
(`.github/workflows/tests.yml`) runs the whole suite on macOS with Python
3.9 and 3.13 on every push to `main` and every pull request.
