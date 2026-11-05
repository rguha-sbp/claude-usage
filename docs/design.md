# Claude Code usage dashboard, statusline and alerts: design

Date: 2026-09-08
Status: implemented. Code comments cite this document as "spec Sec N".

## 1. Goal

Reduce Claude Code token usage for a single engineer without slowing the workflow, by making usage visible in three places:

1. A local web dashboard (month view, sessions view, projects view) with month-end projection, sessions identified by title rather than ID, and per-session cost-reduction tips.
2. A Claude Code statusline showing live context size in absolute tokens with colour thresholds.
3. macOS notifications when context crosses thresholds, and before auto-compact.

Success: within three seconds of opening the dashboard the owner (or anyone they share a screenshot with) can see whether the month is on track against last month, whether spend above the context threshold is falling, and which recent sessions cost most and why. Spend above threshold should visibly fall over subsequent months.

## 2. Findings that shaped the design

From the owner's own transcripts (8 sessions, September 2026): 1.33 B cache-read tokens, 28 M cache-write, 2.6 M output; 72% of assistant calls ran with context above 200K; two sessions reached the 1M window with at most two compactions. Cost is therefore dominated by context size times call count. Output tokens are under 0.2% of the total, so output trimming (caveman) is out of scope.

Facts verified against documentation on 2026-09-08:

- The statusline command receives JSON on stdin including `model.id`, `model.display_name`, `session_id`, `session_name`, `transcript_path`, `cwd`, `workspace.project_dir`, `cost.total_cost_usd`, `context_window.total_input_tokens`, `context_window.context_window_size`, `context_window.used_percentage`, `context_window.current_usage.*`, `exceeds_200k_tokens`, `prompt_cache.hit_ratio`, `effort.level`. It runs after every assistant message and after `/compact`, debounced 300 ms. Optional `refreshInterval` and `padding`.
- Hooks receive no token counts. PreCompact receives `session_id`, `transcript_path`, `cwd`, `hook_event_name` and can be matched on trigger `auto` or `manual`.
- Settings: `cleanupPeriodDays` (default 30), `autoCompactWindow` (tokens), `autoCompactEnabled`.
- The 1M-context model variant is billed at standard per-token pricing across the whole window. There is no premium tier above 200K. The 200K threshold is a linear-cost budget, not a price cliff.
- ccusage v20 (native binary) provides `daily`, `weekly`, `monthly`, `session`, `blocks`, `statusline` with `--json`, `--breakdown`, `--since/--until`, `--offline`, and pricing overrides in `~/.claude/ccusage.json`. `--instances` (per-project grouping) no longer exists in v20. Its dedupe is first-seen-wins on message id + request id, which can undercount when intermediate streaming snapshots are written.
- OAuth usage-endpoint menu bar apps return no data for Enterprise seats and are unverified for Team; the Claude Code Usage Monitor labels Team as an unverified estimate. Both are out of scope. `/usage` inside Claude Code already shows seat usage with attribution.
- AppleScript `display notification` silently fails on macOS 26 unless Script Editor has notification permission. terminal-notifier 3.1 (2026-08-30) works and is called by absolute path.

## 3. Decisions

| Decision | Choice |
|---|---|
| Billing context | Subscription or Team seat; no hard limit being managed; visibility only |
| Headline unit | Dollars first, tokens second |
| Dashboard surface | Local web page, Python stdlib server, vanilla HTML/CSS/JS, no npm |
| Visual direction | iOS liquid glass, "Trends Report" (arrow, verdict sentence, magnitude); follows macOS light/dark; both appearances designed |
| Threshold basis | Absolute tokens, configurable, default 200K |
| Threshold KPI | "Spend above threshold" is a headline metric |
| Session list | Flat, newest first, with project and working directory columns; grouping deferred |
| Cost source | ccusage JSON; "unavailable" when absent, never estimated |
| Persistence | SQLite, so history survives transcript cleanup |
| Out of scope | Menu bar app, usage monitor, ccflare, caveman output trimming |

## 4. Architecture

Five independent pieces, all in this directory, all read-only over Claude Code's data. Nothing runs inside a Claude Code request path except the statusline script.

```
usage/                Python package (stdlib only)
  config.py           loads config.json; thresholds, port, paths, ccusage command
  parser.py           incremental JSONL parser -> session and daily aggregates
  store.py            SQLite schema, upserts, queries
  cost.py             runs ccusage --json, joins costs to sessions and days
  projection.py       month-to-date, projection, last-month comparison, trends
  tips.py             heuristics -> per-session and month-level tips with savings
  server.py           http.server: static files + /api/summary, background refresh
  __main__.py         `python3.15 -m usage serve|refresh|report`
dashboard/
  index.html, app.js, styles.css
statusline/
  statusline.py       stdin JSON -> one coloured line; threshold notifications
hooks/
  precompact-notify.sh
config.json           shared thresholds and settings (see 8)
tests/                pytest; fixtures/ holds small synthetic JSONL transcripts
usage.db              SQLite (gitignored)
docs/design.md        this spec
```

### 4.1 Parser

Input: every `*.jsonl` under `~/.claude/projects/*/` and `~/.claude/projects/*/<session>/subagents/`. `CLAUDE_CONFIG_DIR` is honoured if set.

Incremental: the `files` table stores path, size, mtime and the byte offset consumed. On refresh, a file is re-read from its stored offset if it grew, and from zero if it shrank or its mtime moved backwards.

Record handling:

- `ai-title` sets the session title. Fallbacks in order: a custom name if present in the transcript, the first user prompt truncated to 80 characters, the session ID.
- `cwd` on the first record that has one sets the working directory; project name is the basename of the git root if `workspace.project_dir` is present, else the basename of `cwd`. Worktree paths keep their full directory so they remain distinguishable.
- `assistant` records: usage is read from `message.usage`. Records sharing a `message.id` are merged keeping the maximum of each usage field (intermediate streaming snapshots undercount otherwise). Each merged call contributes: context size = input + cache_creation + cache_read; output; thinking; model.
- `user` records: a real turn if content is a string or contains no `tool_result`; a tool result otherwise. Tool-result byte size is summed. `isCompactSummary` increments compactions.
- Subagent transcripts are parsed the same way and attributed to the parent session found in the directory path.
- Idle gaps: consecutive assistant calls more than 60 minutes apart count as a cold restart; the cache_creation tokens of the first call after the gap are recorded as rebuild tokens.
- Re-reads: `tool_use` blocks named `Read` with the same `file_path` three or more times in one session are counted.

Session aggregate fields: session_id, title, project, cwd, first_ts, last_ts, models (JSON), turns, calls, tool_calls, compactions, input_tokens, cache_write_tokens, cache_read_tokens, output_tokens, thinking_tokens, peak_context, calls_over_threshold, tokens_over_threshold, tool_result_bytes, context_bytes_estimate, subagent_tokens, cold_restarts, rebuild_tokens, repeated_reads (JSON), updated_at.

`tokens_over_threshold` = sum over calls of max(0, context − threshold). Because it depends on the configured threshold, it is recomputed from a per-call table (`calls`: session_id, ts, model, context, output) rather than stored only as an aggregate, so a threshold change in config takes effect on the next refresh without re-reading transcripts.

Daily aggregate: date (local timezone), project, model, tokens by type, calls, calls_over_threshold, tokens_over_threshold.

### 4.2 Store

SQLite file `usage.db` next to the package. Tables: `files`, `sessions`, `calls`, `daily`, `costs_session`, `costs_daily`, `meta`. Upserts keyed by session_id / (date, project, model). `costs_daily` holds ccusage daily totals per model; per-project daily cost is an allocation derived at query time (see 4.3). Sessions never delete: when Claude Code cleans transcripts up after `cleanupPeriodDays`, the rows remain and are flagged `source_present = 0`.

### 4.3 Cost engine

Verified against ccusage 20.0.20 on 2026-09-08. Runs `ccusage session --json --offline` and `ccusage daily --json --offline` (command configurable). Actual shapes:

- session: `{"session": [{"period": "<sessionId>", "totalCost", "inputTokens", "cacheCreationTokens", "cacheReadTokens", "outputTokens", "totalTokens", "modelsUsed": [...], "modelBreakdowns": [{"modelName", "cost", "inputTokens", "cacheCreationTokens", "cacheReadTokens", "outputTokens"}], "metadata": {"lastActivity"}}], "totals": {...}}`. There is no project path field; the join is on `period` = session ID.
- daily: `{"daily": [{"period": "YYYY-MM-DD", "totalCost", "modelBreakdowns": [...], ...}], "totals": {...}}`. The `--instances` flag does not exist in v20, so ccusage gives no per-project daily cost.

Per-project cost is therefore allocated, not reported: each session's ccusage cost is spread across the days it was active in proportion to weighted tokens per day (weights: input 1, cache write 1.25, cache read 0.1, output 5, matching Anthropic's published price ratios). Month and daily totals shown on the Month view come straight from ccusage; project figures are labelled "allocated". Session cost joined on session ID. Per-model month split comes from the daily `modelBreakdowns`.

If the command is missing or exits non-zero, costs are left null and `/api/summary` carries `cost_available: false`; the UI renders "unavailable" in every dollar slot and switches the trend strip to token magnitudes. Blended price per token for a session = session cost / total tokens, used only for approximate savings in tips and always labelled "≈".

Optional: `~/.claude/ccusage.json` pricing overrides if the org has negotiated rates. Not part of the initial build.

### 4.4 Projection and trends

- Month to date: sum of daily cost for the current calendar month, local time.
- Pace: mean daily cost over the last 7 calendar days including today.
- Projection: month to date + pace × remaining days.
- Comparison: previous calendar month total, and previous month's share of spend above threshold.
- Trend arrow: up (bad) if projection exceeds last month by more than 5%, down (good) if below by more than 5%, flat otherwise. Same rule for above-threshold share in percentage points (2 pt band). If the previous month has fewer than 7 days of data, arrows render muted with "no comparison yet".
- Third metric on the Month view: the most expensive session of the month with its share of the month.

### 4.5 Tips

Each tip has: id, title, one-sentence explanation with the session's own numbers, and an approximate saving where computable. Triggers:

| id | Trigger | Saving estimate |
|---|---|---|
| compact-earlier | calls_over_threshold > 0 | tokens_over_threshold × blended price |
| delegate-exploration | tool_result_bytes / context_bytes_estimate > 0.5 and subagent_tokens / total < 0.15 | none (qualitative) |
| cold-cache | cold_restarts ≥ 2 | rebuild_tokens × (write price − read price), using ccusage model prices if available, else omitted |
| split-session | calls > 300 and compactions ≤ 1 | none |
| repeated-reads | any file read ≥ 3 times | none |
| thinking-heavy | thinking_tokens / output_tokens > 0.3 | none |

Every tip also carries `doc`, a link to the Claude Code documentation section that backs its advice; the dashboard shows it under the tip (section 14).

Month-level tips aggregate: total approximate saving from compact-earlier across sessions. (Revised 2026-09-10: the tool alerts only and never recommends or sets `autoCompactWindow`; the earlier pointer to that setting was removed.)

### 4.6 Server and page

`python3.15 -m usage serve` binds `127.0.0.1:<port>` (default 8765). Serves `dashboard/` statically and `GET /api/summary?month=YYYY-MM` returning JSON: month figures, projection, trends, daily series (actual and projected), model split, token-type split, sessions (all fields plus tips), projects, `cost_available`, `threshold`, `generated_at`. A background thread refreshes parser and costs every 60 seconds; requests never trigger parsing.

The page implements the approved mockup: large title with month, segmented tabs Month / Sessions / Projects addressable by URL hash, Auto / Light / Dark control persisted in localStorage, glass panels, trend strip (arrow, verdict sentence, magnitude, footnote), daily bars with faded projection and threshold share, model and token-type splits, title-led session table with working directory, detail panel with tips, projects table. Rows keep DOM identity across refreshes; a numeric cell that changed since the page was last focused gets a held highlight and a "+$x since you looked" note until the user interacts with the row. Charts are inline SVG rendered from data. `prefers-reduced-transparency` and `prefers-reduced-motion` are honoured. Colour is never the sole carrier of state.

### 4.7 Statusline

`statusline/statusline.py` reads stdin JSON and prints one line, for example:

```
fable-5 · Fix the login redirect · ctx 187K/1M (19%) · $12.40 · cache 98% · effort high
```

Colour of the `ctx` segment: green below `warn`, yellow from `warn` to `alert`, red at or above `alert`. Falls back to `context_window.used_percentage` if token fields are absent. Missing fields are omitted, never rendered as errors. Exit code always 0; any exception prints a plain fallback line.

Notifications: state file `~/.claude/usage-state/<session_id>.json` holds `announced` (none | warn | alert) and the title. On each run: if context ≥ alert and announced ≠ alert, notify "Context 180K in <title>: compact now" and set alert; else if context ≥ warn and announced = none, notify "Context 150K in <title>: wrap up and /compact" and set warn; if context < warn and announced ≠ none, reset to none (a compaction happened). Notification command: `/opt/homebrew/bin/terminal-notifier -title "Claude Code" -message ... -group <session_id>`; failure to notify is ignored. State files older than 7 days are pruned opportunistically.

### 4.8 PreCompact hook

`hooks/precompact-notify.sh`, registered in `~/.claude/settings.json` under `PreCompact` with matcher `auto`. Reads stdin JSON with `jq`, looks up the title from the statusline state file by `session_id`, notifies "Auto-compact starting: <title>". Exit 0 always; it observes, never blocks.

### 4.9 Settings changes (user-level `~/.claude/settings.json`)

- `statusLine`: `{ "type": "command", "command": "<python> <repo>/statusline/statusline.py", "padding": 0 }`, where `<python>` is the absolute Python 3.15 path install.sh resolves (16.1)
- `hooks.PreCompact`: matcher `auto`, command path to `precompact-notify.sh`
- `autoCompactWindow`: not set (revised 2026-09-10: an earlier installer wrote 200000; the installer now removes that exact value and never writes one)
- `cleanupPeriodDays`: 90

Thresholds default: warn 150000, alert 180000, dashboard threshold 200000. All in `config.json`.

## 5. Overhead audit (one-time)

Outside the tool: once, check `/context` for MCP servers, skills and hooks that load on every message but are not used, and close idle sessions that hold a 1M context (the next message re-reads all of it).

## 6. Error handling

- Malformed JSONL lines are skipped and counted; the count appears in `/api/summary.warnings`.
- A transcript that shrinks is re-parsed from zero.
- ccusage failures degrade to "unavailable"; the server logs the stderr once per failure streak.
- Server exceptions return HTTP 500 with a JSON body; the page shows the last good data with a "stale since" note.
- Statusline and hook never exit non-zero and never print stack traces to the statusline.

## 7. Testing

Test-driven with pytest and small synthetic JSONL fixtures under `tests/fixtures/`:

- parser: title fallbacks, cwd/project derivation, message-id merge keeps maxima, compaction count, tool-result bytes, subagent attribution, cold restarts, repeated reads, incremental offset behaviour (append, shrink).
- threshold arithmetic: tokens_over_threshold for a set of calls at two thresholds.
- projection: month boundaries, 7-day pace, "no comparison yet" rule, arrow bands.
- tips: each trigger fires and does not fire on boundary inputs; savings arithmetic.
- cost: join behaviour with a canned ccusage JSON; missing binary path yields `cost_available: false`.
- server: starts on an ephemeral port, `/api/summary` returns the schema, static index served.
- statusline: sample stdin JSON renders expected segments and colours; threshold crossing writes state and invokes a stubbed notifier exactly once; reset after compaction.
- hook: sample PreCompact JSON invokes the stubbed notifier with the title.

Manual acceptance: run against the real transcripts, compare session token totals with `ccusage session --json`, open the page in light and dark, trigger a notification by lowering `warn` temporarily.

## 8. Configuration (`config.json`)

```json
{
  "port": 8765,
  "threshold_tokens": 200000,
  "statusline": { "warn_tokens": 150000, "alert_tokens": 180000 },
  "ccusage_command": ["npx", "-y", "ccusage@20.0.48"],
  "claude_dir": "~/.claude",
  "notifier": "/opt/homebrew/bin/terminal-notifier",
  "refresh_seconds": 60
}
```

## 9. Operations

- Start: `uv run usage serve` (or `python3.15 -m usage serve`) from this directory, then open `http://127.0.0.1:8765`.
- Optional launchd agent `~/Library/LaunchAgents/com.example.claude-usage.plist` running the same command at login; documented, not installed by default.
- Prerequisites: uv and Python 3.15+ (install.sh installs 3.15 with uv if it is missing; the macOS system python3 is 3.9 and too old), `brew install terminal-notifier`, Node for `npx ccusage` (or `npm i -g ccusage`). First notification requires granting terminal-notifier permission in System Settings.

## 10. Deferred

Session grouping by project; per-project budgets; pricing overrides; a Claude Code `/usage`-based seat gauge; CSV export.

## 11. Revision 2026-09-14: day periods with hourly bars, and a wide layout

Requested on 2026-09-14: the page leaves the sides of a large monitor empty, and the lowest period the dashboard can show is a month. This revision adds a day period, with the whole page (trend cards, chart, splits, sessions, projects, skills, tips) computed for that day, and lets the layout use the width it is given.

### 11.1 Period model

A **period** is either a month `YYYY-MM` or a day `YYYY-MM-DD`. It replaces `month` as the one argument that scopes the page.

- Every store query that today filters with `day LIKE '<month>-%'` filters with `day LIKE '<period>%'` instead. Because `day` is always ten characters, a month prefix matches its days and a day prefix matches exactly that day. Sessions, projects, skills, tips and the trend cards therefore work for a day with no new SQL.
- The server accepts `/api/summary?period=...` and validates it against `^\d{4}-\d{2}(-\d{2})?$`; anything else is a 400. `?month=` stays as an alias for a month period.
- The payload gains `period` and `kind` (`"month"` or `"day"`), keeps `month` (the containing month) and `months`, and adds `days`: the days of the containing month that have data, for the day picker. The chart series is renamed from `daily` to `series`, each point `{label, value, over_value, projected, tokens, over_tokens}` where `label` is a day for a month period and an hour `"00".."23"` for a day period.

### 11.2 Hourly rollup

Hours must be local, like `day`. SQLite cannot convert the UTC `ts` to local time, so:

- `calls` gains `hour INTEGER` (local hour, 0-23), stamped at insert time next to `day` by a `local_hour(ts)` sibling of `local_day`. Schema version 4 adds the column and forces the usual full re-parse so history is stamped.
- The `daily` rollup table becomes `rollup(day, hour, project, model, ...)` with the same measures and the same `recompute` SQL grouped one level finer. Month consumers sum across hours; nothing they compute changes.
- Cost per hour is the day's ccusage cost allocated by that hour's share of the day's weighted tokens, the same rule already used to allocate a day's cost to projects. Tokens above threshold per hour use the existing per-call rule.

### 11.3 Projection and trends for a day

`compute_month` becomes `compute_period`. For a month it is unchanged. For a day:

- `to_date` is the day's total, `projected` equals `to_date` and `pace` is the day's total; nothing is projected forward and the "today" marker is not drawn. Hours after the current hour on today's date carry no bar.
- The previous period is the previous calendar day. The comparison shows when that day has any data (`MIN_PREVIOUS_DAYS` applies to months only). Verdicts read "running above yesterday" or "above Saturday 13 September" for older days.
- `top_session` is the session with the largest value among sessions active that day.

### 11.4 Page

- Header: the month select stays; a second select beside it lists "Whole month" plus each day in `days`, newest first. Choosing a day switches the period; choosing "Whole month" returns to the month.
- Chart: in a month period, each bar is a button; clicking it opens that day. In a day period the chart shows 24 hourly bars labelled every three hours, the title reads "Hourly spend" or "Hourly tokens", and the hint gives the day's total instead of the seven-day pace.
- The period travels in the query string (`?period=2026-09-14`), where the month already lived; the hash keeps its existing job of naming the pane and the selected session. A day view is bookmarkable and survives reload.
- Copy: a `periodLabel` helper gives "September 2026" or "Sunday 14 September 2026"; "this month" becomes "today" for the current day and "on this day" otherwise, in headings, empty states, table hints and tips. `month_tips` takes the period phrase as an argument.

### 11.5 Wide layout

- `.page` max width rises from 1120px to 1680px with fluid side padding.
- At 1400px and above: the Month tab's chart row and splits keep their 2:1 ratio but fill the width; the Sessions tab widens the table column to 2:1 against the detail panel; the Skills panel table gets the full row.
- Below 900px the existing single-column stacking is unchanged.

### 11.6 Testing

- Store: `local_hour`, v3 to v4 migration adds the column and forces re-parse, rollup grouped by hour, prefix filters for a day.
- Projection: a day period's totals, previous-day comparison with and without data, hourly cost allocation summing to the day's cost, no projected points.
- Summary and server: payload shape for both kinds, `period` validation, `month` alias.
- Dashboard static: day select and series ids present, no remaining "this month" literal that bypasses the helper.
- Screenshots at 1440px and 2560px for both a month and a day, via headless Chrome as before.

### 11.7 Out of scope

Hourly cost from ccusage directly (it reports per day), and a week period.

## 12. Revision 2026-09-14: the Office tab

Requested on 2026-09-14: a Civilization-style isometric office in which every live Claude Code session is a small worker, animated while working and asleep with z's when not, grouped into rooms by project ("departments"), with a click opening that session. Decisions taken during brainstorming: isometric authored SVG (no sprite packs); live sessions only, one worker per session, rooms per project; state comes from the statusline alone, no new hooks; the terminal cannot be reached from the page (the sessions run in VS Code's integrated terminal), so a click opens the session in the dashboard and shows what it is waiting for.

### 12.1 Presence, stamped by the statusline

Claude Code runs `statusline/statusline.py` after every assistant message. Each run already reads and rewrites `<state_dir>/<session_id>.json` (`announced`, `title`). It now also writes:

- `seen`: ISO-8601 UTC timestamp of this run.
- `cwd`: the session's working directory from the statusline JSON.
- `model`: `model.display_name`, falling back to `model.id`.
- `transcript_path`: from the statusline JSON, so the office can read the transcript tail without deriving a path.
- `pid`: the process id of the nearest ancestor process whose command is `claude`, found by walking up from the statusline's own parent (one `ps -axo pid=,ppid=,comm=` call). Missing when it cannot be found; the office then treats the worker as alive while `seen` is fresh.

The stamp happens on every run, including runs without a context size (today the file is only written from `check_thresholds`). Timing budget stays "milliseconds": the `ps` call is measured in the tests' timing check and skipped if the environment variable `USAGE_NO_PS` is set. Binding rules unchanged: exit 0 always, never raise, no network, no transcript parsing. `prune_state` (7 days) unchanged.

### 12.2 Office endpoint

`GET /api/office` (usage/office.py, served by usage/server.py) scans `state_dir/*.json` and returns:

```json
{"generated_at": "...", "working_seconds": 60,
 "rooms": [{"project": "my-app", "cwd": "~/Code/my-app",
            "workers": [{"session_id": "...", "title": "...", "model": "...",
                         "state": "working" | "asleep", "since": "...",
                         "last_message": "..." | null}]}]}
```

- A file is a candidate when it has `seen` and `cwd`. It is **gone** when `pid` is present and `os.kill(pid, 0)` says the process no longer exists (`ProcessLookupError`); gone workers are omitted. Without a `pid`, a file whose `seen` is older than `office.gone_after_seconds` (default 86400) is omitted.
- **working** when `now - seen <= office.working_seconds` (default 60); otherwise **asleep**. `since` is `seen` for a sleeper (when it fell quiet) and the first `seen` inside the current working streak for a worker, which the endpoint keeps in memory per session id.
- Room = `parser.derive_project(cwd)`; `cwd` is abbreviated like the rest of the dashboard. Rooms are ordered by worker count then name; workers by title.
- `last_message` for sleepers only: the text of the last `assistant` record in the transcript at the stamped `transcript_path`, found by reading the final 64 KB of the file, truncated to 400 characters; `null` when the path is missing or unreadable. Reading stays inside `<claude_dir>`: a `transcript_path` outside it is ignored.
- Liveness (`os.kill`) and the clock are injectable so tests use a fake state directory and a fake process table.
- The endpoint touches no database and takes the app lock only to read config.

Config additions (`config.json`, all optional): `"office": {"working_seconds": 60, "gone_after_seconds": 86400}`.

### 12.3 The scene

A fourth tab, **Office**, in the header segment (Month / Sessions / Projects / Office), addressable as `#office`. Rendered by `dashboard/office.js`, a second script loaded after `app.js`; `app.js` only registers the pane and calls into it. Styles in a new `dashboard/office.css`.

Isometric SVG drawn from the payload, no Canvas, no game loop:

- The scene is one `<svg>` sized to its panel, redrawn on each poll and on resize. A 2:1 isometric projection: tile (x, y) maps to screen (`(x - y) * w/2`, `(x + y) * h/2`).
- Each **room** is a floor slab of `ceil(sqrt(n))` by `ceil(n / cols)` desk tiles with a low back wall along two edges carrying the project name. Rooms pack left to right and wrap, separated by one tile of corridor.
- Each **desk** is a box with a monitor; each **worker** is an authored figure (head, torso, arms) built from a handful of paths, coloured from a small palette keyed by model so the same model always wears the same colour. Titles are drawn as a short label under the desk, truncated.
- **Working**: arms bob (CSS keyframes on the arm group), monitor glows. **Asleep**: torso rotated onto the desk, monitor dim, three "z" text glyphs rising and fading in a loop. **Gone**: not drawn; the room shrinks on the next poll.
- All icons and glyphs are authored SVG text and paths; the existing "no emoji glyphs" test keeps applying to `office.js`.
- Under `prefers-reduced-motion: reduce` every animation is disabled and the poses are static. This tab is the one deliberate exception to the design system's single motion moment (recorded in the README's Office paragraph; there is no DESIGN.md in this repo).
- Revision 2026-09-15 (game-ai skill applied): behaviour is split into decide / steer / path in `dashboard/office-sim.js`, DOM-free. Decide: a state machine per worker with transitions inside the states: Typing, Thinking, Dozing, Walk(target, then), Drinking, Chatting(partner), Leaving, Gone. Only sleepers wander (cooler, or a chat with another sleeper in the same room); a working session never leaves its desk; a gone session walks to the doorway and fades. Steer: `arrive` in tile space, speed 1.7 tiles/s, frame-rate independent, 14 s stuck timeout. Path: desk -> aisle -> along the aisle -> target, computed once per errand. Rendering: seated workers live in their desk row's layer, walkers in a layer above every desk, furniture (plant, cooler, doorway) in the aisle drawn after the rows; a squash-and-stretch plays on every state change; figures get hair, glasses and coat from a hash of the session id and a chair, mug (working) and glowing screen. Reduced motion disables wandering and all animation. Verified by `tests/office_sim_check.js` under node (wrapped by tests/test_office_sim.py) since headless Chrome here has no frame loop.
- Revision 2026-09-16 (the campus): the world is no longer only rooms. `dashboard/office-world.js` (DOM-free) owns the layout and every static shape; `dashboard/office.js` keeps the fetch, the frame loop, the figures and the hover card. Rooms pack along u = x - y and wrap; the plaza and the server room take the front row, the plaza centred under the offices, and a **corridor lane** at a constant v = x + y runs in front of every room. A *spot* carries everything the simulation needs about a place: `{x, y, lane, via}`, where `via` are the waypoints from the worker's own aisle. A trip to the canteen is then the same code path as a trip to the water cooler, and the walk home is the route already walked, reversed. The plaza holds a coffee bar with standing room (one worker per spot, claimed first come first served), benches, plants and the notice board; the server room holds racks.
- As built: rooms are placed along the screen-horizontal axis (constant x + y in tile space) so they sit side by side and wrap into a second row when the panel is too narrow; the scene is drawn at 1:1 and may grow up to 1.6x on a wide panel, never more; name plates are drawn in a final layer above every desk with a light halo so a plate is never hidden by the desk in front of it; desks sit 1.5 tiles apart.

### 12.4 Interaction

- Each worker group is `tabindex="0"`, `role="button"`, with an `aria-label` reading "<title>, <state> since <time>".
- Hover or focus shows a card (HTML, positioned over the SVG) with title, model, state and since when, and for a sleeper the `last_message` excerpt under the heading "Waiting on you".
- Click or Enter opens the Sessions tab with that session selected. If the current period does not include today, the period is reset to the current month first (the session is live, so it has calls today). This reuses `selectSession` and `choosePeriod` from `app.js`, exposed on a small `window.usageDashboard` object.
- The Office tab polls `/api/office` every 5 seconds while it is the visible pane and stops when it is not; the 60-second summary poll is unchanged. A failed poll keeps the last scene and shows the existing stale banner.

### 12.5 Testing

- Statusline: stamps `seen`, `cwd`, `model`, `pid` on a run with and without a context size; missing fields never raise; `USAGE_NO_PS` skips the `ps` call; the run stays under the existing timing check.
- Office: classification of working / asleep / gone against a fake state dir, fake clock and fake process table; rooms grouped and ordered; `since` tracks the working streak; `last_message` from a transcript tail, `null` when missing; endpoint JSON shape and 200.
- Dashboard static: `tab-office`, `pane-office`, `office-scene` ids present; `office.js` and `office.css` linked and exist; the three DOM-free files load before the controller and contain no `document.`; no emoji glyphs; `office.js` reads only keys the office payload produces (same scan as the summary test, bound to `room` and `worker`, plus the `board` the server joins on); the tier and campus classes are styled; both stylesheets have balanced braces (a stray `}` silently drops the rule that follows it, which cost one debugging round on the plaza floor).
- Node harnesses (skipped when node is missing): `office_sim_check.js` drives 240 simulated seconds and asserts working sessions never leave their desk, sleepers run errands, two workers from different projects meet and talk at the canteen, a lounger stands exactly on its spot, a gone session walks out, and the swift tier outpaces the rest without breaking the speed ceiling; `office_cast_check.js` checks the tier table and all 24 hours of the sky; `office_world_check.js` checks that no two blocks overlap, the plaza sits in front of the offices, every canteen route ends on the corridor lane, and the drawn scene has no `NaN` or `undefined` in it.
- Screenshots at 1440px and 2560px with two rooms and mixed states from a seeded state dir, via headless Chrome; the night and dusk lighting is stilled by rendering `OfficeWorld.draw` under node into a standalone page, since headless Chrome here has no frame loop.

### 12.6 Revision 2026-09-16: model tiers

`dashboard/office-cast.js` maps a model to a tier by substring, so both the display name ("Opus 5 (1M context)") and the id ("claude-opus-5") land in the same place. An unknown or missing model is an ordinary worker; nothing throws.

| tier | model | gets |
| --- | --- | --- |
| `lead` | Opus | crown, trophy on the desk, gold-trimmed desk and chair, a slow halo |
| `muse` | Fable | monocle, silk coat, orchid on the desk, gold trim, a halo |
| `core` | Sonnet, anything unknown | the plain worker |
| `swift` | Haiku | 0.86 scale, 1.35x walking speed, a quicker step cycle, headphones |

The tier is the single source for all of it: `office.js` reads `crown`, `aura`, `ornament`, `chair` and `scale` when it draws, `office-sim.js` reads `speed` so a small model really does move quicker. Shirt colour stays keyed to the model's name hash, hair, glasses and coat to the session id.

### 12.7 The notice board

A board on the plaza carries the day's real numbers: spend so far (tokens when ccusage is unavailable), the busiest project, and how many sessions are awake. `board_stats(store, day, cost_available)` in `usage/summary.py` returns `{day, unit, value, tokens, top_project, top_value}` from the day's rollup and allocated costs; the `/api/office` handler attaches it as `board`. `Office` itself stays database-free, so its tests keep using nothing but a fake state directory.

### 12.8 Time of day

`skyOf(hour)` in `office-cast.js` returns a palette for the viewer's local hour: dawn (05-08), day (08-17), dusk (17-20), night (20-05). It drives a gradient behind the campus, a `brightness()` on everything in the world, stars at night, and desk lamps plus a street lamp on the plaza from dusk. Name plates are drawn in an overlay outside the lighting so they stay readable after dark.

### 12.9 Out of scope

Typing into a session from the page (no path to VS Code's terminal; Ghostty 1.3 would allow it via AppleScript if sessions moved there); permission-prompt state (needs the Notification hook); subagents as helpers; sound.

## 13. Revision 2026-10-14: sub-agents in the Office

Requested on 2026-10-08: the Office should show which sessions are working with sub-agents and as much as can be known about each one. Brainstorming decisions: a **live operations view** (not history, not cost forensics); **transcripts only**, no new hooks, consistent with the alerts-only rule; sub-agents drawn as **linked characters** in the scene **plus a readable Agents rail**, the rail being the source of truth and the scene the glance. This supersedes "subagents as helpers" in 12.9.

Prior art surveyed: Pixel Agents (VS Code extension; sub-agents spawn as characters linked to their parent, bubbles for waiting), disler's multi-agent observability (per-agent event timeline, stable colours per agent), claude-esp and June (TUIs listing sub-agents with live transcripts), Claude Code's own OpenTelemetry traces (`agent_id` / `parent_agent_id`, beta only). None breaks down per-sub-agent usage from the transcripts, which is what this section adds.

### 13.1 What the transcripts give

A session's sub-agents live beside its transcript: `<transcript without .jsonl>/subagents/agent-<id>.jsonl` and `agent-<id>.meta.json`. The meta file holds `agentType`, `description`, `toolUseId` (the parent's `Agent` tool_use id), `spawnDepth` and `requestShape` (`"background"` for async launches). Every record in the agent transcript has `isSidechain: true`, `agentId`, `timestamp`, and assistant records carry `message.model`, `message.usage` and `tool_use` blocks.

### 13.2 `usage/agents.py`

Database-free and office-free. `AgentReader` keeps, per agent transcript and per lead transcript of a session with sub-agents, a byte offset and running totals, and on each call reads only the bytes appended since (the parser's append-only assumption); a file that shrank is re-read from 0. A cut final line is left for the next read (the offset stops before it). Per agent it produces a typed record:

| field | source |
|---|---|
| `agent_id` | file stem without `agent-` |
| `type`, `description`, `background`, `depth` | meta.json `agentType`, `description`, `requestShape == "background"`, `spawnDepth` (default 1) |
| `parent` | the `agent_id` whose transcript holds an `Agent`/`Task` tool_use with this agent's `toolUseId`; `null` = the main session |
| `model` | latest assistant `message.model` |
| `tokens` | sum of `_usage_from` (parser) over assistant records, input + cache write + cache read + output, each `message.id` counted once (the last occurrence wins, as streamed chunks repeat it) |
| `tools`, `tool_mix` | count of tool_use blocks, and count per tool name; `SubagentHandback` is not counted (it is the hand-back, not work) |
| `doing` | last tool_use as `{tool, target}`, target the first of `file_path`, `command`, `pattern`, `url`, `query`, `description`, trimmed to 80 chars; `{tool: "replying"}` when the last assistant record is text only; a record with only thinking leaves `doing` as it was |
| `started`, `last` | first and last record `timestamp` |
| `state` | `done` once the agent has called `SubagentHandback` (how current Claude Code sub-agents finish; checked against 39 local transcripts), or, for older transcripts, when the last assistant record is text only, its parent has the result, and nothing has been written for 10 seconds; else `running` while `last` is within the Office's working window (60 s), or within 15 minutes while the agent is busy; else `asleep` (section 14). Busy means one of the agent's own tool calls has no `tool_result` with its `tool_use_id` yet (a long Bash beside parallel Reads that have answered), or its parent (the lead, or the agent that spawned it) has the `Agent`/`Task` call with this agent's `toolUseId` still open, or its turn is still open: its last record is a `tool_result`, or an assistant record without a `stop_reason` (Claude Code writes one record per content block and sets `stop_reason` on the last block only; checked against 335 local transcripts of Claude Code 2.1.304 and later, where no stopped agent ended either way). The model writes no record while it composes a long reply, so a quiet file alone does not mean idle. A background agent's call is answered at launch, so only its own calls count; an interrupted agent's call is answered with the interruption, so it sleeps once quiet |

An agent without a meta file, or whose transcript holds no record yet, is skipped. `session_agents(transcript_path, now)` returns every running and asleep agent plus the done agents whose `last` is within 2 hours, capped at the 20 most recent done ones, and the count of done ones left out (`hidden`). Paths outside `claude_dir` are ignored, as in 12.2.

### 13.3 Office payload

`last_assistant_message` becomes `tail_activity(path, claude_dir)` returning `{last_message, doing}` from the same tail read; `doing` uses the same rule as 13.2. Each worker gains:

- `doing`: the lead's own current tool while it works, or `null` (a sleeping lead shows none, section 14).
- `agents`: the flat list from 13.2 (the tree is built by the client from `parent`).
- `agents_hidden`: the `hidden` count.

`last_message` keeps its meaning (only for sleepers). `Office` owns one `AgentReader`; `server.py` is unchanged. Dollars per sub-agent are not shown: ccusage prices sessions, and dollars are never estimated (section 3).

### 13.4 The scene

- **Pods.** `office-world.js` gives each desk a pod of 4 standing slots beside it, emitted as `places[sid].pod` (spots, so the existing routing walks interns there). Desk pitch along a room's width grows from 1.5 to 2.25 tiles for every room, so geometry does not jump when an agent spawns. Running interns take slots in spawn order; beyond 4 the lead's name plate shows `+N`.
- **Interns.** The worker figure at 0.72 scale with a lanyard badge coloured by a hash of `agentType` (stable per type); model-tier accessories apply. A tether line joins each intern to its actual parent figure (lead or intern), dashed for background agents.
- **Tool families.** One table in `office-cast.js`, used for leads and interns: read (Read, Grep, Glob, LS: holding a page), edit (Edit, Write, MultiEdit, NotebookEdit: typing), shell (Bash, BashOutput: terminal glyph), web (WebFetch, WebSearch: globe), delegate (Agent, Task: pointing at its child), mcp (`mcp__*`: plug), anything else or replying: thinking.
- **Intern behaviour** (`office-sim.js`, next to the worker machine, same `arrive` steering): Arriving (doorway to slot; the lead pops), Working (pose follows the tool family, changing only when the family changes), Napping (dozing in its slot while its agent is asleep), HandingBack on done (walks to the lead's desk, a beat, the lead pops), Leaving (out of the doorway, fade). Whether a lead works is the server's call alone (section 14).
- **Hover card.** An intern shows type, description, model, elapsed, tokens, tool count and `doing`; a lead gains its `doing` line and "N interns running". Clicking an intern opens the parent session.
- **Reduced motion.** Interns appear in their slots, tethers drawn, no walking, glyphs static.

### 13.5 The Agents rail

A collapsible 360px rail right of the scene (under it below 1100px), one block per live session in room order. The header reads title, model, working/quiet time, `doing`, and "N running · M done · tokens across sub-agents". Below it the agents as a tree by `parent`, each row: state glyph (running, done, stalled, background), type, description, model, elapsed, tokens, tools and `doing`; `tool_mix` appears on hover or focus; done and stalled rows are dimmed; `+N earlier` closes the list when `agents_hidden > 0`. Rows and figures share `data-agent` and highlight each other on hover. A row click opens the parent session. Re-rendered only when a signature of ids, states and `doing` changes; elapsed times tick from the client clock. Glyphs are authored SVG.

### 13.6 Files

New `usage/agents.py`, `dashboard/office-figures.js` (DOM-free: `figure`, `headgear`, the intern figure, moved out of `office.js`), `dashboard/office-rail.js`. Changed `usage/office.py`, `office-cast.js`, `office-world.js`, `office-sim.js`, `office.js`, `office.css`, `index.html`.

### 13.7 Testing

- `tests/test_agents.py` on tmp fixture folders: meta fields; parent linking through a nested `toolUseId`; token sums equal to the parser's for the same records, with a repeated `message.id` counted once; `doing` targets; running / done / stalled; incremental reads (append, only new lines are parsed) and a shrunk file re-read; a cut last line; missing meta skipped; the 2-hour window, cap of 20 and `hidden`.
- `test_office.py`: `tail_activity`; the new worker keys.
- Static: new scripts loaded before `office.js`, DOM-free ones without `document.`; `office-rail.js` reads only keys the payload produces; no emoji glyphs.
- Node: `office_sim_check.js` (an intern arrives, works, hands back and leaves; a lead with running interns never leaves its desk); `office_world_check.js` (pod slots overlap no desk or each other); new `office_figures_check.js` (no `NaN` / `undefined`).
- Screenshots at 1440px and 2560px from a seeded state dir with nested sub-agents.

## 14. Revision 2026-11-05: sleep, interns, tips and hardening

Requested on 2026-11-05: idle sessions and sub-agents must look asleep, only leads wear the crown, workers walk to the cooler only while they work, and every tip must cite the Claude Code documentation.

- **Lead state.** The server decides it alone. A lead is `working` when its statusline stamp is within `office.working_seconds`, or while it waits on a `running` sub-agent that is not a background one (13.2: running while the lead's `Agent` call is open, up to 15 minutes of quiet); otherwise `asleep`, and `doing` is `null`. A lead whose own window has passed while only a background agent runs is `asleep` and shows no tool: the background agent's work is not its lead's. The client no longer upgrades a lead because an intern is busy.
- **Sub-agent state.** `running`, `asleep` or `done` (13.2). `stalled` is gone; a quiet agent that is not busy (13.2: no open tool call of its own, no parent still waiting on it, and its turn closed), or busy but quiet for 15 minutes, is `asleep` and its intern naps in its slot. The rail marks it with a `z` glyph and dims the row.
- **Intern dress.** `office-cast.js` `internTierOf(model)` is the model's tier without the crown and the halo; other accessories stay.
- **Breaks.** A sleeper never leaves its desk. After a Thinking beat a working lead takes a break with probability 0.3 (canteen, aisle chat with a working colleague, or the cooler). A break is an errand: the moment the session stops working the walker goes home and dozes, or walks out if the session is gone.
- **Tips.** Each tip has `doc`, a `https://code.claude.com/docs/en/...#section` link, and its text was rewritten to say only what that page says (checked 2026-11-05): compacting at a natural break (prompt-caching), subagents keeping verbose output out of the main context while their own calls still count, the cache lifetime of one hour on a subscription and five minutes on an API key, `/clear` between unrelated tasks, specific prompts needing fewer file reads, `/effort` for simpler tasks, and loaded skills staying in the conversation.
- **Hardening.** The server answers 403 unless `Host` is `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding), the name compared without case and with one trailing dot allowed (`localhost.`). The installer shell-quotes the repo path in the statusline and hook commands and still recognises its own hook when quoted. `ccusage` is pinned to `20.0.48` instead of `@latest`.
- **CI.** `.github/workflows/tests.yml` runs pytest (which drives the node checks) on macOS with Python 3.9 and 3.13 (revised in 16: 3.15 only).

## 15. Revision 2026-11-05: the Office in three.js

Requested on 2026-11-05: replace the SVG campus with a three.js open-plan office floor, decorate it heavily but cleanly, offer a few themes to switch between, and make the people look like people. The server and the `/api/office` payload do not change. This replaces the SVG drawing of sections 12 and 13: `office-world.js`, `office-figures.js` and their node checks are gone.

### 15.1 The floor plan

`dashboard/office-floor.js` `build(data, theme)` is pure (no DOM, no three.js) and returns a plan in tile space (one tile is about a metre; x across, y from the back wall to the front):

- a back band (server room, meeting room, kitchen, lounge, library), the main corridor, then bands of neighbourhoods, one per project, and a right column (a second meeting room, the printer corner, reception and the entrance door);
- a neighbourhood is a fenced block of desk units: the desk, the lead's chair, and a pod of `POD` (4) standing slots for interns; a sign names the project and a water cooler stands at the end of the first row;
- `places[session_id]` are the spots the simulation walks between (seat, cooler, two meeting spots, the pod, the handoff, the door and the canteen spots), each `{x, y, lane, via}` with every lane a horizontal aisle line;
- decor is placed last and only where it touches no desk, partition, walk or spot. The floor grows to fit, the figures never shrink. Places do not depend on the theme.

### 15.2 The simulation on the floor

`office-sim.js` keeps its contract (12.3, 13.4, 14). Every shared spot (canteen tables, the cooler stand, the two meeting spots, a desk's hand-off spot) is held by one agent at a time through `freeSpot`, keyed by its position; pod slots stay assigned by `freeSlot` for an intern's whole stay. Two more changes. `GRIP = 8` in the `arrive` steering: without it a walker overshot at a corner and cut across furniture. And a leaver walks out from wherever it is: the door's `via` starts at the worker's own aisle, so `walkOut` follows the route the worker is on, back or ahead, to the nearest point of its way out (seat, aisle, the door's `via`, the door), then folds the out-and-back stretches. Every leg is one the floor already keeps clear. A re-layout (a session joins or leaves; the simulation sees a new `places` object) restarts every agent whose places moved, compared structurally, at its new seat, whatever it was doing: its position, route and target belong to the old floor, and only the new seat is known to be clear. `tests/office_floor_check.js` drives the simulation over real floors and fails on anyone who walks through a desk or a piece of decor, including sessions that end at the canteen, at another row's cooler or halfway along a walk, and a re-layout during a break.

### 15.3 Themes

`office-themes.js` is pure data: Studio (pale wood planks, planter partitions), Loft (concrete, brick partitions, warm lamps) and Greenhouse (terracotta tiles, vine partitions, a planted bed). A theme sets the palette by role, the floor pattern, the partition style, the decor weights and density, the centrepiece and the light. People are not themed. The picker in the panel head stores the choice in `localStorage` (`usage.office.theme`); `?office=<key>` previews a theme until the reader picks one (the pick drops the parameter) and `?hour=<0-23>` previews a time of day.

### 15.4 Scenery

- `office-kit.js`: unit shapes, the `Batch` that turns every static part into one `InstancedMesh` per (layer, shape), the material cache and canvas textures.
- `office-decor.js`: one builder per decor kind and the desk.
- `office-scenery.js`: the floor canvas, walls, windows, partitions, rooms, signs, the notice board and the desk screens. `setDesk(id, {working, title, extra})` lights a screen and writes the name plate and the `+N` counter, and `setBoard(lines)` writes the board, all without a rebuild; a label repaints only when its text changes. A rebuild disposes what it built, never the shared shapes, materials and textures (`shared` in `office-kit.js`).

Static shadows are drawn once per rebuild (`shadowMap.autoUpdate = false`); figures use blob shadows.

### 15.5 People

`office-people.js` `Person` is a jointed figure (hips, knees, spine, shoulders, elbows, hands, a nodding head) with hair styles, glasses, a beard on some leads, and the tier's headgear (crown, monocle, headphones). Each simulation pose has a pose in `POSES` (typing, thinking, dozing, walking, waving, drinking, lounging, chatting, standing, handing, gone), blended over 0.35 s, with props (a cup, a tablet, a folder). A tool glyph, a speech bubble and z's float above the head. A leaver waves near the door.

### 15.6 The view

`office-3d.js` `OfficeView` owns the renderer, an orthographic isometric camera fitted to the floor, the light (hemisphere plus a sun, scaled by the theme and the viewer's clock), picking, drag to pan and wheel to zoom (1 to 4, about the pointer). A rebuild of a floor with the same footprint keeps the reader's pan and zoom. The loop runs only while the Office pane and the page are visible: full rate while anyone walks or the pointer drags, about 15 frames a second otherwise. Under reduced motion it draws one frame per update. Without WebGL the pane says so.

three r187 is vendored in `dashboard/vendor/three` (the npm tarball's `three.module.js`, `three.core.js` and `LICENSE`, with sha256 values in `VERSION`) and resolved through an import map, so the dashboard needs no network.

### 15.7 Controller, accessibility and tests

`office.js` is an ES module: it fetches every 5 s, feeds the simulation and the view (the plan and the scenery are rebuilt only when the theme, the light or the rooms' sessions and tiers change), and owns the hover card, the theme picker, the Agents rail links and a screen-reader roster of buttons (one per worker). The empty-office note replaces the canvas only once the last figure has walked out. Tests: `tests/office_floor_check.js` (every floor size in every theme), `tests/office_themes_check.js` (complete themes, names the scenery draws), static checks for the import map and the vendored hashes, and `tests/test_office_browser.py`, which loads the office in headless Chrome against the real server and fails without a rendered frame or on a console error (skipped without Chrome).

## 16. Revision 2026-11-05: Python 3.15 and Dependabot

Requested on 2026-11-05: run on the latest Python, and let Dependabot cover every dependency, the vendored three.js included.

### 16.1 Python 3.15 and the pinned interpreter

`requires-python` is `>=3.15` and `.python-version` says `3.15`, so `uv run` and CI use 3.15. The code uses the 3.15 idiom: builtin generics, `X | None`, `type` aliases and `datetime.fromisoformat` reading a `Z` suffix itself.

Claude Code runs the statusline command through a shell on every prompt. A bare `python3` there is the macOS system 3.9, which cannot import this code. `uv run` would pick the right version but adds its startup to every prompt. So install.sh resolves one interpreter with `uv python find --system 3.15`, after `uv python install 3.15` when none is found, and writes its absolute, shell-quoted path into `statusLine`. `--system` skips the repo `.venv`, which `uv` may recreate or the reader may delete. uv's path names the minor version (`cpython-3.15-macos-aarch64-none/bin/python3.15`, checked 2026-11-05), so a patch upgrade of 3.15 keeps it valid. If the interpreter is removed, the statusline fails visibly and the README troubleshooting says to re-run `install.sh`. `merge_settings` takes the interpreter as a parameter and stays pure; re-running the installer replaces an older `python3 ...` entry. Without uv the installer fails at once with a message, because every later Python step needs the interpreter. The PreCompact hook is bash and needs no Python.

### 16.2 Dependabot

`.github/dependabot.yml` checks `npm`, `uv` and `github-actions` weekly. A root `package.json` pins `three` exactly to the vendored version; it has no scripts and exists so Dependabot can see the copy in `dashboard/vendor/three`. `tests/test_dashboard_static.py` fails when `package.json`, `package-lock.json` and `dashboard/vendor/three/VERSION` disagree, so a Dependabot bump stays red until someone runs `scripts/vendor_three.py`. That script downloads the tarball from registry.npmjs.org, checks its sha512 against `package-lock.json`, reads only the members `package/build/three.module.js`, `package/build/three.core.js` and `package/LICENSE` by exact name (no other member is ever extracted, and a member that is not a regular file is rejected) and rewrites `VERSION`.
