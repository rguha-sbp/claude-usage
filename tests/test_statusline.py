import io
import json

from statusline.statusline import (
    check_thresholds,
    colour_for,
    context_tokens,
    main,
    make_notifier,
    prune_state,
    read_state,
    render,
    write_state,
)

SAMPLE = {
    "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"},
    "session_id": "abc-123", "session_name": "SSM params missing from main",
    "cost": {"total_cost_usd": 12.4},
    "context_window": {"total_input_tokens": 999, "context_window_size": 1000000, "used_percentage": 18.7,
                       "current_usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 186000}},
    "prompt_cache": {"hit_ratio": 0.98},
    "effort": {"level": "high"},
}


def test_context_tokens_prefers_current_usage_then_percentage():
    assert context_tokens(SAMPLE) == 187002
    assert context_tokens({"context_window": {"context_window_size": 200000, "used_percentage": 50}}) == 100000
    assert context_tokens({}) is None


def test_colour_for_thresholds():
    assert colour_for(10, 150000, 180000) == "green"
    assert colour_for(150000, 150000, 180000) == "yellow"
    assert colour_for(180000, 150000, 180000) == "red"
    assert colour_for(None, 150000, 180000) == "green"


def test_colour_for_boundaries():
    # exactly at warn-1 is still green, warn is yellow, alert-1 is yellow, alert is red
    assert colour_for(149999, 150000, 180000) == "green"
    assert colour_for(150000, 150000, 180000) == "yellow"
    assert colour_for(179999, 150000, 180000) == "yellow"
    assert colour_for(180000, 150000, 180000) == "red"


def test_render_full_line():
    line = render(SAMPLE, 150000, 180000)
    assert line.startswith("Fable 5.1 · SSM params missing from main · ")
    assert "ctx 187K/1M (19%)" in line and "\033[31m" in line     # red: 187K is past the 180K alert
    assert "$12.40" in line and "cache 98%" in line and line.endswith("effort high")


def test_render_with_missing_fields():
    line = render({"model": {"display_name": "X"}}, 150000, 180000)
    assert line == "X"


def test_render_omits_ctx_segment_when_no_context_info():
    data = {"model": {"display_name": "X"}, "cost": {"total_cost_usd": 1.0}}
    line = render(data, 150000, 180000)
    assert "ctx" not in line
    assert "\033[" not in line


def test_render_green_below_warn():
    data = {"model": {"display_name": "X"},
            "context_window": {"context_window_size": 200000,
                                "current_usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                                    "cache_read_input_tokens": 10000}}}
    line = render(data, 150000, 180000)
    assert "\033[32m" in line and "\033[0m" in line


def test_check_thresholds_announces_once_and_resets(tmp_path):
    sent = []
    notify = lambda t, m, g: sent.append((t, m, g))
    d = json.loads(json.dumps(SAMPLE))

    def with_ctx(n):
        d["context_window"]["current_usage"] = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": n}
        return d
    assert check_thresholds(with_ctx(100000), 150000, 180000, tmp_path, notify) is None
    assert check_thresholds(with_ctx(151000), 150000, 180000, tmp_path, notify) == "warn"
    assert check_thresholds(with_ctx(160000), 150000, 180000, tmp_path, notify) is None
    assert check_thresholds(with_ctx(181000), 150000, 180000, tmp_path, notify) == "alert"
    assert check_thresholds(with_ctx(190000), 150000, 180000, tmp_path, notify) is None
    assert check_thresholds(with_ctx(40000), 150000, 180000, tmp_path, notify) is None      # compaction happened -> reset
    assert read_state(tmp_path, "abc-123")["announced"] == "none"
    assert check_thresholds(with_ctx(152000), 150000, 180000, tmp_path, notify) == "warn"
    assert [m for _, m, _ in sent] == ["Context 150K in SSM params missing from main: wrap up and /compact",
                                        "Context 180K in SSM params missing from main: compact now",
                                        "Context 150K in SSM params missing from main: wrap up and /compact"]
    assert all(g == "abc-123" for _, _, g in sent)
    assert read_state(tmp_path, "abc-123")["title"] == "SSM params missing from main"


def test_check_thresholds_repeated_calls_same_ctx_fire_once(tmp_path):
    sent = []
    notify = lambda t, m, g: sent.append((t, m, g))
    d = json.loads(json.dumps(SAMPLE))
    d["context_window"]["current_usage"] = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 155000}
    assert check_thresholds(d, 150000, 180000, tmp_path, notify) == "warn"
    assert check_thresholds(d, 150000, 180000, tmp_path, notify) is None
    assert check_thresholds(d, 150000, 180000, tmp_path, notify) is None
    assert len(sent) == 1


def test_check_thresholds_missing_session_id_noop(tmp_path):
    sent = []
    notify = lambda t, m, g: sent.append((t, m, g))
    d = json.loads(json.dumps(SAMPLE))
    del d["session_id"]
    d["context_window"]["current_usage"] = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 200000}
    assert check_thresholds(d, 150000, 180000, tmp_path, notify) is None
    assert sent == []
    assert list(tmp_path.glob("*.json")) == []


def test_check_thresholds_title_falls_back_when_session_name_absent(tmp_path):
    sent = []
    notify = lambda t, m, g: sent.append((t, m, g))
    data = {"session_id": "no-title-session",
             "context_window": {"current_usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                                     "cache_read_input_tokens": 200000}}}
    assert check_thresholds(data, 150000, 180000, tmp_path, notify) == "alert"
    assert sent == [("Claude Code", "Context 180K in this session: compact now", "no-title-session")]
    assert read_state(tmp_path, "no-title-session")["title"] == ""


def test_make_notifier_swallows_missing_binary(tmp_path):
    notify = make_notifier(str(tmp_path / "no-such-binary"))
    notify("Claude Code", "hello", "group")  # must not raise


def test_prune_state_removes_old_files_only(tmp_path):
    import os
    import time

    old = tmp_path / "old.json"
    fresh = tmp_path / "fresh.json"
    old.write_text("{}")
    fresh.write_text("{}")
    eight_days_ago = time.time() - 8 * 86400
    os.utime(old, (eight_days_ago, eight_days_ago))
    prune_state(tmp_path, max_age_days=7)
    assert not old.exists()
    assert fresh.exists()


def test_prune_state_missing_dir_is_a_noop():
    from pathlib import Path
    prune_state(Path("/nonexistent/does/not/exist"))  # must not raise


def test_main_prints_line_and_exits_zero(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    out = io.StringIO()
    rc = main(stdin=io.StringIO(json.dumps(SAMPLE)), stdout=out, config_path=cfg, notify=lambda t, m, g: None)
    assert rc == 0 and "ctx 187K/1M" in out.getvalue()
    assert (tmp_path / "state" / "abc-123.json").exists()


def test_main_survives_garbage_input(tmp_path):
    out = io.StringIO()
    rc = main(stdin=io.StringIO("not json"), stdout=out, config_path=tmp_path / "none.json", notify=lambda t, m, g: None)
    assert rc == 0 and out.getvalue().strip() == "claude"


def test_main_falls_back_to_defaults_when_config_load_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "config.json"
    # invalid config: warn_tokens >= alert_tokens makes load_config raise ValueError
    cfg.write_text(json.dumps({"statusline": {"warn_tokens": 200000, "alert_tokens": 100000}}))
    out = io.StringIO()
    rc = main(stdin=io.StringIO(json.dumps(SAMPLE)), stdout=out, config_path=cfg, notify=lambda t, m, g: None)
    assert rc == 0
    line = out.getvalue().strip()
    # still renders, using the hard-coded fallback thresholds (150000/180000) -> 187002 tokens is red/alert
    assert "ctx 187K/1M (19%)" in line
    assert "\033[31m" in line


def test_main_missing_session_id_still_renders_and_writes_nothing(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    data = json.loads(json.dumps(SAMPLE))
    del data["session_id"]
    out = io.StringIO()
    rc = main(stdin=io.StringIO(json.dumps(data)), stdout=out, config_path=cfg, notify=lambda t, m, g: None)
    assert rc == 0 and "ctx 187K/1M" in out.getvalue()
    assert not (tmp_path / "state").exists() or list((tmp_path / "state").glob("*.json")) == []


# --- Fix round 1: review findings -------------------------------------------------

def test_write_state_confines_path_traversal_session_id(tmp_path):
    write_state(tmp_path, "../../evil", {"announced": "alert", "title": "t"})
    # Nothing must escape tmp_path: no file at any ancestor directory.
    assert not (tmp_path.parent / "evil.json").exists()
    assert not (tmp_path.parent.parent / "evil.json").exists()
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert files[0].parent.resolve() == tmp_path.resolve()
    # Round-trips when read back with the same raw (unsafe) session_id.
    assert read_state(tmp_path, "../../evil")["announced"] == "alert"


def test_write_state_confines_slash_session_id_no_subdirectory(tmp_path):
    write_state(tmp_path, "a/b", {"announced": "warn", "title": "t"})
    assert not (tmp_path / "a").exists()  # no subdirectory created
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert read_state(tmp_path, "a/b")["announced"] == "warn"


def test_check_thresholds_path_traversal_id_writes_nothing_outside_and_no_notify(tmp_path):
    sent = []
    notify = lambda t, m, g: sent.append((t, m, g))
    data = {"session_id": "../../evil",
            "context_window": {"current_usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                                    "cache_read_input_tokens": 10000}}}
    assert check_thresholds(data, 150000, 180000, tmp_path, notify) is None
    assert sent == []
    assert not (tmp_path.parent / "evil.json").exists()
    assert not (tmp_path.parent.parent / "evil.json").exists()


def test_check_thresholds_slash_session_id_confined_and_still_notifies(tmp_path):
    sent = []
    notify = lambda t, m, g: sent.append((t, m, g))
    data = {"session_id": "a/b", "session_name": "weird",
            "context_window": {"current_usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                                    "cache_read_input_tokens": 200000}}}
    assert check_thresholds(data, 150000, 180000, tmp_path, notify) == "alert"
    assert not (tmp_path / "a").exists()
    assert len(sent) == 1


def test_write_state_is_atomic_no_tmp_file_left(tmp_path):
    write_state(tmp_path, "sess1", {"announced": "warn", "title": "t"})
    names = sorted(p.name for p in tmp_path.glob("*"))
    assert names == ["sess1.json"]
    assert not any(".tmp-" in n for n in names)
    assert json.loads((tmp_path / "sess1.json").read_text()) == {"announced": "warn", "title": "t"}


def test_context_tokens_coerces_numeric_strings():
    data = {"context_window": {"current_usage": {"input_tokens": "2",
                                                    "cache_creation_input_tokens": "1000",
                                                    "cache_read_input_tokens": "186000"}}}
    assert context_tokens(data) == 187002


def test_render_full_line_with_string_current_usage_values():
    data = json.loads(json.dumps(SAMPLE))
    data["context_window"]["current_usage"] = {"input_tokens": "2", "cache_creation_input_tokens": "1000",
                                                  "cache_read_input_tokens": "186000"}
    line = render(data, 150000, 180000)
    assert "ctx 187K/1M (19%)" in line and "\033[31m" in line
    assert "$12.40" in line and "cache 98%" in line and line.endswith("effort high")


def test_context_tokens_null_current_usage_field_falls_back_to_percentage():
    data = {"context_window": {"context_window_size": 200000, "used_percentage": 50,
                                "current_usage": {"input_tokens": None,
                                                    "cache_creation_input_tokens": 1000,
                                                    "cache_read_input_tokens": 186000}}}
    assert context_tokens(data) == 100000


def test_render_non_numeric_current_usage_omits_only_ctx_segment(tmp_path):
    data = {"model": {"display_name": "X"}, "session_name": "S",
            "context_window": {"current_usage": {"input_tokens": "abc",
                                                    "cache_creation_input_tokens": 0,
                                                    "cache_read_input_tokens": 0}},
            "cost": {"total_cost_usd": 1.5}, "prompt_cache": {"hit_ratio": 0.5},
            "effort": {"level": "low"}}
    line = render(data, 150000, 180000)
    assert "ctx" not in line
    assert "\033[" not in line
    assert line == "X · S · $1.50 · cache 50% · effort low"


def test_check_thresholds_keeps_last_known_title_when_session_name_missing(tmp_path):
    sent = []
    notify = lambda t, m, g: sent.append((t, m, g))
    with_title = {"session_id": "sid-1", "session_name": "My Session",
                  "context_window": {"current_usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                                          "cache_read_input_tokens": 10000}}}
    assert check_thresholds(with_title, 150000, 180000, tmp_path, notify) is None
    assert read_state(tmp_path, "sid-1")["title"] == "My Session"

    without_title = {"session_id": "sid-1",
                     "context_window": {"current_usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                                             "cache_read_input_tokens": 200000}}}
    assert check_thresholds(without_title, 150000, 180000, tmp_path, notify) == "alert"
    assert read_state(tmp_path, "sid-1")["title"] == "My Session"
    assert sent == [("Claude Code", "Context 180K in My Session: compact now", "sid-1")]


# --- Fix round 2: review findings -------------------------------------------------

def test_main_state_file_lands_under_claude_config_dir_env(tmp_path, monkeypatch):
    """CLAUDE_CONFIG_DIR, when set, must override config.json's claude_dir for the
    statusline too (fix round 2), so it agrees with install.sh and the PreCompact
    hook about where the state file (and its title) lives. Uses monkeypatch.setenv
    with a tmp_path directory only -- never the real home/~/.claude."""
    claude_dir = tmp_path / "alt-claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))

    cfg = tmp_path / "config.json"
    # No state_dir override in config.json: it must be derived from claude_dir,
    # and claude_dir itself must come from the env var even though config.json
    # does not set claude_dir at all here (the env var wins regardless).
    cfg.write_text(json.dumps({}))

    out = io.StringIO()
    rc = main(stdin=io.StringIO(json.dumps(SAMPLE)), stdout=out, config_path=cfg, notify=lambda t, m, g: None)

    assert rc == 0 and "ctx 187K/1M" in out.getvalue()
    assert (claude_dir / "usage-state" / "abc-123.json").exists()


# ---------------------------------------------------------------- presence (spec Sec 12.1)
from statusline.statusline import find_claude_pid, stamp_presence  # noqa: E402

PRESENCE_INPUT = {"session_id": "s-live", "session_name": "Fix the thing", "cwd": "/Users/x/proj",
                  "transcript_path": "/Users/x/.claude/projects/-Users-x-proj/s-live.jsonl",
                  "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"}}


def test_stamp_presence_writes_seen_cwd_model_transcript_and_pid(tmp_path):
    stamp_presence(PRESENCE_INPUT, tmp_path, pid_finder=lambda: 4242, now="2026-09-15T08:00:00+00:00")
    state = json.loads((tmp_path / "s-live.json").read_text())
    assert state["seen"] == "2026-09-15T08:00:00+00:00"
    assert state["session_id"] == "s-live"
    assert state["cwd"] == "/Users/x/proj"
    assert state["model"] == "Fable 5.1"
    assert state["transcript_path"].endswith("s-live.jsonl")
    assert state["pid"] == 4242


def test_stamp_presence_keeps_existing_fields_and_tolerates_missing_ones(tmp_path):
    (tmp_path / "s-live.json").write_text(json.dumps({"announced": "warn", "title": "Old title", "pid": 7}))
    stamp_presence({"session_id": "s-live"}, tmp_path, pid_finder=lambda: None, now="2026-09-15T08:00:00+00:00")
    state = json.loads((tmp_path / "s-live.json").read_text())
    assert state["announced"] == "warn" and state["title"] == "Old title"
    assert state["seen"] == "2026-09-15T08:00:00+00:00"
    assert state["pid"] == 7                      # an unknown pid never erases a known one
    assert "cwd" not in state and "model" not in state


def test_stamp_presence_without_session_id_writes_nothing(tmp_path):
    stamp_presence({"cwd": "/x"}, tmp_path, pid_finder=lambda: 1, now="2026-09-15T08:00:00+00:00")
    assert list(tmp_path.glob("*.json")) == []


def test_main_stamps_presence_even_without_context_info(tmp_path):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    out = io.StringIO()
    main(stdin=io.StringIO(json.dumps({"session_id": "s9", "cwd": "/Users/x/proj"})), stdout=out,
         config_path=cfgp, notify=lambda *a: None)
    state = json.loads((tmp_path / "state" / "s9.json").read_text())
    assert state["cwd"] == "/Users/x/proj" and "seen" in state


def test_find_claude_pid_walks_up_the_process_table():
    table = {500: (400, "python3"), 400: (300, "sh"), 300: (200, "claude"), 200: (1, "zsh")}
    assert find_claude_pid(500, table) == 300
    assert find_claude_pid(200, table) is None
    assert find_claude_pid(999, table) is None


def test_find_claude_pid_is_skipped_when_ps_is_disabled(monkeypatch):
    monkeypatch.setenv("USAGE_NO_PS", "1")
    assert find_claude_pid(1) is None
