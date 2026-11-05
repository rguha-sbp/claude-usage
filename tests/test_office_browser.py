"""Loads the office in headless Chrome against the real server and checks
that the three.js floor renders without a console error: the import map,
the vendored three and every office module have to resolve for the canvas
to get `data-rendered="1"`. Skipped when Chrome is not installed."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from usage.config import load_config
from usage.server import UsageServer
from usage.store import Store

CHROME = next((p for p in (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"))
    if p and Path(p).exists()), None)


@pytest.mark.skipif(CHROME is None, reason="Chrome is not installed")
def test_office_floor_renders_in_a_browser(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                                "state_dir": str(state), "ccusage_command": ["ccusage"],
                                "refresh_seconds": 3600}))
    cfg = load_config(cfgp)
    transcript = tmp_path / "projects" / "-x" / "s1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    # a live session (this test's own pid) puts one worker on the floor
    (state / "s1.json").write_text(json.dumps({
        "session_id": "s1", "title": "T", "cwd": "/x/proj", "model": "Opus 5",
        "seen": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), "transcript_path": str(transcript)}))
    srv = UsageServer(cfg, Store(cfg.db_path), runner=lambda cmd: (1, "", "none"), port=0)
    srv.refresh_once()
    srv.start()
    try:
        proc = subprocess.run(
            [CHROME, "--headless=new", "--user-data-dir=%s" % (tmp_path / "chrome"), "--disable-extensions",
             "--no-first-run", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
             "--window-size=1400,1000", "--virtual-time-budget=6000", "--enable-logging=stderr", "--v=0",
             "--dump-dom", "http://127.0.0.1:%d/#office" % srv.port],
            capture_output=True, text=True, timeout=90)
    finally:
        srv.stop()
    errors = [line for line in proc.stderr.splitlines()
              if "CONSOLE" in line and "127.0.0.1" in line and ("Error" in line or "Uncaught" in line)]
    assert not errors, errors
    assert 'data-rendered="1"' in proc.stdout, "the office canvas never drew a frame"
