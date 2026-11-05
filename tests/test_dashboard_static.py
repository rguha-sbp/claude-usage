"""Static checks on the dashboard bundle.

There is no JS runtime in the test suite, so these tests guard the two things
that silently rot: the element ids `app.js` reaches for, and the payload keys
it reads. The second test builds a real `/api/summary` payload and asserts
that every `data.<key>` / `summary.<key>` / `sess.<key>` / `pt.<key>` /
`pr.<key>` access in `app.js` names a key that `build_summary` actually
produces.
"""
import hashlib
import html as html_lib
import json
import re
from datetime import date
from pathlib import Path

from tests.helpers import assistant_msg, ai_title, iso, make_session, user_msg
from usage.config import REPO_ROOT, load_config
from usage.pipeline import refresh
from usage.store import Store
from usage.summary import build_summary

DASH = REPO_ROOT / "dashboard"
IDS = ["tabs", "theme", "pane-month", "pane-sessions", "pane-projects", "pane-office", "tab-office",
       "office-view", "office-theme", "office-roster", "office-card", "office-hint", "title-month", "subtitle",
       "trend-strip", "chart", "chart-legend", "split-model", "split-tokens", "movers-body",
       "sessions-body", "detail", "projects-body", "month-tips", "foot-note", "stale",
       "skills-body", "skills-hint", "office-rail", "office-rail-list", "office-rail-toggle"]


def _html():
    return (DASH / "index.html").read_text()


def _css():
    return (DASH / "styles.css").read_text()


def _js():
    return (DASH / "app.js").read_text()


# The DOM-free office files are classic scripts that node can drive; the
# three.js side (kit, decor, scenery, people, the view) is ES modules under
# office.js.
DOM_FREE = ["format.js", "office-cast.js", "office-themes.js", "office-floor.js", "office-sim.js", "office-rail.js"]
OFFICE_MODULES = ["office.js", "office-3d.js", "office-kit.js", "office-decor.js", "office-scenery.js", "office-people.js"]
OFFICE_JS = DOM_FREE[1:] + OFFICE_MODULES


def _office_js():
    return "\n".join((DASH / name).read_text() for name in OFFICE_JS)


def test_index_has_required_ids_and_assets():
    html = _html()
    for i in IDS:
        assert 'id="%s"' % i in html, i
    assert 'href="styles.css"' in html and 'src="app.js"' in html
    assert 'href="office.css"' in html and '<script type="module" src="office.js">' in html
    assert (DASH / "office.css").is_file()
    # the cast (model tiers, daylight), the themes, the floor plan and the
    # simulation (state machines, steering) are DOM-free files, all loaded
    # before the controller module that uses them
    for name in DOM_FREE:
        assert (DASH / name).is_file(), name
        assert html.index('src="%s"' % name) < html.index('src="office.js"'), name
        source = (DASH / name).read_text()
        assert "document." not in source and "getElementById" not in source, "%s must not touch the DOM" % name
    # format.js (esc, fmtTokens) precedes app.js and every office file; the
    # cast (tiers, families) precedes the files that read it at load time
    for name in ["app.js"] + DOM_FREE[1:] + ["office.js"]:
        assert html.index('src="format.js"') < html.index('src="%s"' % name), name
    for name in ("office-themes.js", "office-floor.js", "office-sim.js", "office-rail.js"):
        assert html.index('src="office-cast.js"') < html.index('src="%s"' % name), name
    assert html.index('src="office-themes.js"') < html.index('src="office-floor.js"')
    for name in OFFICE_MODULES[1:]:
        assert (DASH / name).is_file(), name
        assert 'src="%s"' % name not in html, "%s is imported by office.js, not loaded by a tag" % name
    assert '<meta name="color-scheme" content="light dark">' in html


def test_index_has_no_inline_event_handlers():
    """Behaviour lives in app.js; markup carries no on*= attributes."""
    html = _html()
    found = re.findall(r"\son(?:click|change|input|load|submit|mouse\w+|key\w+)\s*=", html)
    assert not found, found


def test_index_sessions_table_has_a_skills_column():
    html = _html()
    sessions_pane = html.split('id="pane-sessions"', 1)[1].split('id="pane-projects"', 1)[0]
    assert "<th scope=\"col\" class=\"r\">Skills</th>" in sessions_pane


def test_index_has_month_selector_and_skeleton():
    html = _html()
    assert 'id="month-select"' in html, "header needs a month selector (payload.months)"
    assert 'id="day-select"' in html, "header needs a day selector (payload.days)"
    assert "skeleton" in html, "first paint must show a skeleton, not an empty page"


def test_css_has_both_appearances_and_accessibility_queries():
    css = _css()
    assert '[data-theme="dark"]' in css and "prefers-color-scheme: dark" in css
    assert "prefers-reduced-transparency" in css and "prefers-reduced-motion" in css
    assert "backdrop-filter" in css and "--threshold" in css


def test_css_derives_both_appearances_from_one_tint_seed():
    css = _css()
    assert "--seed-h" in css, "one tint seed must drive both appearances"
    # The seed is declared once; every appearance derives from it.
    assert len(re.findall(r"--seed-h\s*:", css)) == 1


def test_css_themes_selection_and_scrollbars():
    css = _css()
    assert "::selection" in css
    assert "scrollbar-color" in css or "::-webkit-scrollbar" in css


def test_js_uses_summary_fields_and_polls():
    js = _js()
    for key in ["cost_available", "spend_trend", "over_trend", "top_session", "display_title",
                "display_cwd", "peak_context", "tokens_over_threshold", "model_split",
                "token_split", "month_tips", "projected", "skills", "skills_carried_tokens",
                "carried_tokens", "injected_tokens"]:
        assert key in js, key
    assert "/api/summary" in js
    assert "localStorage" in js
    assert re.search(r"setInterval\([^,]+,\s*60000\)", js), "page must poll every 60 s"


def test_js_has_no_emoji_or_unicode_glyph_icons():
    """Craft floor: icons are authored inline SVG, never emoji/Unicode glyphs.

    Entities are decoded first so `&rarr;` cannot smuggle an arrow past the
    scan, and there is no whitelist: an arrow or a chevron must be a path.
    """
    source = html_lib.unescape(_js()) + html_lib.unescape(_office_js()) + html_lib.unescape(_html())
    bad = [c for c in source if ord(c) > 0x2100]
    assert not bad, "unicode glyphs used as icons: %r" % sorted(set(bad))


def _payload(tmp_path) -> dict:
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                                "ccusage_command": ["ccusage"], "refresh_seconds": 3600}))
    cfg = load_config(cfgp)
    make_session(tmp_path, "-proj", "s1", [
        user_msg("hello", iso(0)),
        ai_title("Investigate the thing"),
        assistant_msg("m1", iso(1), cache_read=300000, output=200),
        assistant_msg("m2", iso(2), cache_read=400000, output=200),
    ])
    store = Store(cfg.db_path)
    refresh(store, cfg, runner=lambda cmd: (1, "", "no ccusage"))
    return build_summary(store, cfg, None, date(2026, 9, 8), ["a warning"])


def test_js_only_reads_keys_build_summary_produces(tmp_path):
    payload = _payload(tmp_path)
    assert payload["sessions"], "fixture must produce a session"
    allowed = {
        "data": set(payload.keys()),
        "payload": set(payload.keys()),
        "summary": set(payload["summary"].keys()),
        "sess": set(payload["sessions"][0].keys()),
        "pt": set(payload["series"][0].keys()),
    }
    if payload["projects"]:
        allowed["pr"] = set(payload["projects"][0].keys())
    # Comments are prose, not property access ("usage/summary.py" must not
    # read as summary.py), so strip them before scanning.
    js = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", _js(), flags=re.S))
    for var, keys in allowed.items():
        used = set(re.findall(r"\b%s\.([A-Za-z_][A-Za-z0-9_]*)" % var, js))
        unknown = used - keys
        assert not unknown, "app.js reads %s.<key> not produced by build_summary: %s" % (
            var, sorted(unknown))
        if var in ("data", "sess"):
            assert used, "expected app.js to read some %s.<key>" % var


def test_dashboard_files_are_served(tmp_path):
    """Every asset index.html links must exist under the served dashboard dir."""
    html = _html()
    for name in re.findall(r'(?:href|src)="([^"#:]+)"', html):
        assert (DASH / name).is_file(), name
    assert Path(DASH / "styles.css").is_file() and Path(DASH / "app.js").is_file()
def test_css_reduced_transparency_block_wins_over_later_glass_rules():
    """`.seg`/`.picker` re-declare `background`/`backdrop-filter` at the same
    specificity as the reduced-transparency override, so the override must be
    declared after them or it silently loses the cascade."""
    css = _css()
    query = css.rindex("@media (prefers-reduced-transparency: reduce)")
    for selector in (".seg, .picker {", ".seg .thumb {", ".tips li {"):
        assert css.index(selector) < query, selector
    block = css[query:]
    for selector in (".glass", ".seg", ".picker"):
        assert selector in block, selector
    assert "backdrop-filter: none" in block


def test_css_has_a_text_safe_threshold_stop():
    """The bright threshold orange is a mark colour; text uses a darker stop."""
    css = _css()
    # declared once per appearance: light, [data-theme=dark], prefers dark
    assert len(re.findall(r"--threshold-text\s*:", css)) == 3
    # no rule paints text with the mark-weight orange
    assert "color: var(--threshold);" not in css
    assert "color:var(--threshold)" not in _js()
    assert 'fill="var(--threshold)"' not in _js()


def test_js_draws_today_marker_only_when_something_is_projected():
    js = _js()
    assert "hasProjected" in js
    assert re.search(r"if \(hasActual && hasProjected", js), "today marker must require a projection"
    assert re.search(r"hasProjected \? '<span><i style=\"background:var\(--bar-proj\)", js), \
        "the Projected legend entry must require a projection"


def test_js_hides_previous_share_when_there_is_no_comparison():
    js = _js()
    m = re.search(r"var wasShare = \((.{0,220}?)\)\s*\n?\s*\? ''", js, re.S)
    assert m, "wasShare guard not found"
    assert "over_trend.direction === 'none'" in m.group(1)


def test_js_resyncs_the_url_when_the_detail_falls_back():
    js = _js()
    body = js[js.index("function renderDetail"):js.index("function renderProjects")]
    assert "var previous = state.selected;" in body
    assert body.count("syncUrl();") >= 2, "both the empty and the fallback path must resync"


def test_office_js_only_reads_keys_the_office_payload_produces(tmp_path):
    """Same guard as above for the office scripts: `data.<key>` is the
    /api/office payload, `room.<key>` a room, `worker.<key>` a worker and
    `sub.<key>` one of its sub-agents."""
    from dataclasses import fields
    from usage.agents import SubAgent
    from usage.office import Office
    transcript = tmp_path / "projects" / "-x" / "s1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    (tmp_path / "s1.json").write_text(json.dumps({
        "session_id": "s1", "title": "T", "cwd": "/x/proj", "model": "Fable 5.1",
        "seen": "2026-09-15T08:00:00+00:00", "pid": 1, "transcript_path": str(transcript)}))
    payload = Office(tmp_path, tmp_path, alive=lambda pid: True).payload()
    assert payload["rooms"], "fixture must produce a worker"
    # the server joins the notice board onto the payload (spec Sec 12.7)
    allowed = {"data": set(payload.keys()) | {"board"}, "room": set(payload["rooms"][0].keys()),
               "worker": set(payload["rooms"][0]["workers"][0].keys()),
               "sub": {f.name for f in fields(SubAgent)}}
    js = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", _office_js(), flags=re.S))
    for var, keys in allowed.items():
        used = set(re.findall(r"\b%s\.([A-Za-z_][A-Za-z0-9_]*)" % var, js))
        unknown = used - keys
        assert not unknown, "office scripts read %s.<key> not in the office payload: %s" % (var, sorted(unknown))
        assert used, "expected the office scripts to read some %s.<key>" % var


def test_three_is_vendored_and_mapped_offline():
    """three.js loads from the dashboard itself (no CDN), through an import
    map that office-3d.js's bare `import ... from 'three'` resolves against,
    and the vendored files are the ones VERSION records."""
    html = _html()
    imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html, flags=re.S).group(1))["imports"]
    assert imports == {"three": "./vendor/three/three.module.js"}
    assert html.index('type="importmap"') < html.index('type="module"')
    assert "https://" not in html.split("</head>")[0].split('type="importmap"')[1]
    vendor = DASH / "vendor" / "three"
    pins = re.findall(r"^([0-9a-f]{64})  (\S+)$", (vendor / "VERSION").read_text(), flags=re.M)
    assert {name for _, name in pins} == {"three.module.js", "three.core.js", "LICENSE"}
    for digest, name in pins:
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == digest, name


def test_office_people_pose_every_sim_state():
    """Every pose the simulation can set has a pose in office-people.js, so
    no state renders as a figure frozen in the default stance."""
    sim = (DASH / "office-sim.js").read_text()
    people = (DASH / "office-people.js").read_text()
    block = people[people.index("const POSES = {"):people.index("\n};", people.index("const POSES = {"))]
    poses = set(re.findall(r"^  (\w+):", block, flags=re.M))
    used = set(re.findall(r"pose = '(\w+)'", sim)) | set(re.findall(r"pose: '(\w+)'", sim))
    assert used, "expected the simulation to set some poses"
    assert used <= poses, "poses with no figure: %s" % sorted(used - poses)


def test_office_css_has_night_and_reduced_motion():
    css = (DASH / "office.css").read_text()
    assert ".office-stage.night" in css and "--sky-top" in css and "--sky-bottom" in css
    assert "prefers-reduced-motion" in css
    for cls in (".office-view", ".vacant", ".office-theme", ".office-rail", ".rail-row", ".linked"):
        assert cls in css, cls


def test_stylesheets_are_balanced():
    """A stray `}` makes the browser drop the rule that follows it, which is
    invisible in the markup and only shows up as an unstyled shape."""
    for name in ("styles.css", "office.css"):
        css = (DASH / name).read_text()
        depth = 0
        for i, ch in enumerate(css):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                assert depth >= 0, "%s: unmatched } at character %d" % (name, i)
        assert depth == 0, "%s: %d block(s) left open" % (name, depth)
