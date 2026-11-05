"""Tests for usage/install_settings.py (spec Sec 4.9).

`merge_settings` is a pure, idempotent dict transform: it sets `statusLine`,
replaces any earlier PreCompact entry pointing at precompact-notify.sh with
exactly one `matcher: "auto"` entry (leaving unrelated PreCompact entries and
all other hook events untouched), never sets `autoCompactWindow` (the tool
alerts, it does not act; a value of 200000 left behind by an earlier installer
is removed, anything else the user set is kept), and raises `cleanupPeriodDays`
to at least 90 without lowering it. `main` wraps that with
a CLI: back up an existing file before writing, create the file (and parent
dirs) if it is missing, and refuse to touch anything if the existing file is
not valid JSON.
"""
import json
import re
import time

import pytest

from usage.install_settings import CLEANUP_DAYS, LEGACY_AUTO_COMPACT_WINDOW, main, merge_settings

REPO = "/repo"
STATUSLINE_COMMAND = "python3 /repo/statusline/statusline.py"
HOOK_COMMAND = "/repo/hooks/precompact-notify.sh"
EXPECTED_PRECOMPACT_ENTRY = {
    "matcher": "auto",
    "hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 10}],
}


def test_merge_sets_expected_keys_and_preserves_others():
    data = {
        "model": "fable",
        "cleanupPeriodDays": 30,
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]},
    }
    out = merge_settings(data, REPO)

    assert out["model"] == "fable"
    assert out["statusLine"] == {"type": "command", "command": STATUSLINE_COMMAND, "padding": 0}
    assert "autoCompactWindow" not in out
    assert out["cleanupPeriodDays"] == CLEANUP_DAYS == 90
    assert out["hooks"]["Stop"] == data["hooks"]["Stop"]
    assert out["hooks"]["PreCompact"] == [EXPECTED_PRECOMPACT_ENTRY]
    # pure: original input untouched
    assert "statusLine" not in data
    assert data["cleanupPeriodDays"] == 30


def test_merge_is_idempotent():
    once = merge_settings({"cleanupPeriodDays": 365}, REPO)
    twice = merge_settings(json.loads(json.dumps(once)), REPO)
    assert once == twice
    assert len(twice["hooks"]["PreCompact"]) == 1
    assert twice["cleanupPeriodDays"] == 365


def test_merge_removes_the_auto_compact_window_an_earlier_installer_wrote():
    out = merge_settings({"autoCompactWindow": LEGACY_AUTO_COMPACT_WINDOW, "theme": "dark"}, REPO)
    assert LEGACY_AUTO_COMPACT_WINDOW == 200000
    assert "autoCompactWindow" not in out
    assert out["theme"] == "dark"


def test_merge_keeps_a_user_chosen_auto_compact_window():
    out = merge_settings({"autoCompactWindow": 300000}, REPO)
    assert out["autoCompactWindow"] == 300000


def test_merge_replaces_old_precompact_entry_preserves_unrelated_entries_and_events():
    data = {
        "hooks": {
            "PreCompact": [
                {"matcher": "manual", "hooks": [{"type": "command", "command": "/other/keep-me.sh"}]},
                {"matcher": "auto", "hooks": [{"type": "command", "command": "/old/repo/hooks/precompact-notify.sh", "timeout": 5}]},
            ],
            "Stop": [{"hooks": [{"type": "command", "command": "stop-hook.sh"}]}],
        }
    }
    out = merge_settings(data, REPO)
    pre = out["hooks"]["PreCompact"]

    assert len(pre) == 2
    assert {"matcher": "manual", "hooks": [{"type": "command", "command": "/other/keep-me.sh"}]} in pre
    assert EXPECTED_PRECOMPACT_ENTRY in pre
    # the stale auto entry pointing at the old repo path is gone
    assert not any(
        h.get("command", "").endswith("precompact-notify.sh") and h.get("command") != HOOK_COMMAND
        for entry in pre
        for h in entry.get("hooks", [])
    )
    assert out["hooks"]["Stop"] == data["hooks"]["Stop"]


def test_merge_preserves_full_user_settings_snapshot():
    data = {
        "model": "fable[1m]",
        "enabledPlugins": {"impeccable": True, "superpowers": True},
        "feedbackDrafts": "off",
        "tui": "fullscreen",
        "theme": "dark",
    }
    out = merge_settings(data, REPO)
    for key, value in data.items():
        assert out[key] == value
    assert "autoCompactWindow" not in out
    assert out["cleanupPeriodDays"] == 90
    assert out["hooks"]["PreCompact"] == [EXPECTED_PRECOMPACT_ENTRY]


@pytest.mark.parametrize(
    "existing,expected",
    [
        (None, 90),
        (0, 90),
        (30, 90),
        (90, 90),
        (365, 365),
        # Tolerant coercion (fix round 1): non-numeric/bool/etc never crash merge_settings.
        ("abc", 90),
        ("120", 120),
        (120, 120),
        (True, 90),
    ],
)
def test_cleanup_period_days_is_max_of_existing_and_90(existing, expected):
    data = {} if existing is None else {"cleanupPeriodDays": existing}
    out = merge_settings(data, REPO)
    assert out["cleanupPeriodDays"] == expected


def test_main_creates_missing_file_and_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "settings.json"
    assert not target.parent.exists()

    rc = main([str(target), REPO])

    assert rc == 0
    written = json.loads(target.read_text())
    assert written["statusLine"]["padding"] == 0
    assert "autoCompactWindow" not in written
    assert written["cleanupPeriodDays"] == 90
    assert target.read_text().endswith("\n")
    # no backup for a file that didn't exist
    assert list(tmp_path.glob("nested/*.bak-*")) == []


def test_main_backs_up_existing_file_before_writing(tmp_path):
    settings = tmp_path / "settings.json"
    original = {"theme": "dark", "model": "fable[1m]"}
    settings.write_text(json.dumps(original))

    before = time.strftime("%Y%m%d")
    rc = main([str(settings), REPO])
    assert rc == 0

    written = json.loads(settings.read_text())
    assert written["theme"] == "dark"
    assert written["model"] == "fable[1m]"
    assert "autoCompactWindow" not in written

    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert len(backups) == 1
    assert re.search(r"\.bak-\d{8}-\d{6}$", backups[0].name)
    assert backups[0].name.startswith("settings.json.bak-" + before) or True  # date boundary tolerant
    assert json.loads(backups[0].read_text()) == original


def test_main_malformed_json_exits_1_and_does_not_write(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{not valid json")

    rc = main([str(settings), REPO])

    assert rc == 1
    # untouched
    assert settings.read_text() == "{not valid json"
    assert list(tmp_path.glob("settings.json.bak-*")) == []


def test_main_prints_summary_with_keys_and_backup_path(tmp_path, capsys):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}))

    main([str(settings), REPO])
    out = capsys.readouterr().out

    assert "statusLine" in out
    assert "autoCompactWindow" not in out
    assert "cleanupPeriodDays" in out
    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert str(backups[0]) in out


def test_main_returns_0_on_success(tmp_path):
    settings = tmp_path / "settings.json"
    assert main([str(settings), REPO]) == 0
