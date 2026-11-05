"""Runtime logging: the migration, every refresh and background failures leave a trace."""
import json
import logging
import sqlite3
from pathlib import Path

from tests.helpers import assistant_msg, iso, make_session, user_msg
from usage.config import load_config
from usage.pipeline import refresh
from usage.server import UsageServer
from usage.store import Store
import usage.store as store_mod


def _cfg(tmp_path):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                                "ccusage_command": ["ccusage"], "refresh_seconds": 3600}))
    return load_config(cfgp)


def test_migration_logs_the_version_step_and_the_forced_reparse(tmp_path, caplog):
    db_path = tmp_path / "v2.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(store_mod.SCHEMA)
    raw.execute("INSERT INTO meta(key,value) VALUES('schema_version','3')")
    raw.commit(); raw.close()
    with caplog.at_level(logging.INFO, logger="usage"):
        Store(db_path)
        Store(db_path)  # already migrated: silent
    msgs = [r.getMessage() for r in caplog.records if r.name == "usage.store"]
    assert len(msgs) == 1
    assert "3 -> 4" in msgs[0] and "re-parse" in msgs[0]


def test_refresh_logs_one_summary_line_with_counts_and_each_warning(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0)), assistant_msg("m1", iso(1), cache_read=10)])
    (tmp_path / "projects" / "-p" / "bad.jsonl").write_text("not json\n")
    store = Store(cfg.db_path)
    with caplog.at_level(logging.DEBUG, logger="usage"):
        refresh(store, cfg, runner=lambda cmd: (1, "", "ccusage missing"))
    pipeline = [r for r in caplog.records if r.name == "usage.pipeline"]
    infos = [r.getMessage() for r in pipeline if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "2 file(s)" in infos[0] and "2 session(s)" in infos[0] and "1 call(s)" in infos[0] and "costs unavailable" in infos[0]
    warns = [r.getMessage() for r in pipeline if r.levelno == logging.WARNING]
    assert any("bad.jsonl" in w and "invalid JSON" in w for w in warns)
    assert any("ccusage missing" in w for w in warns)
    debug = [r.getMessage() for r in caplog.records if r.name == "usage.parser" and r.levelno == logging.DEBUG]
    assert any("s1.jsonl" in d and "offset 0" in d for d in debug)


def test_unchanged_files_are_not_logged_again(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0)), assistant_msg("m1", iso(1), cache_read=10)])
    store = Store(cfg.db_path)
    refresh(store, cfg, runner=lambda cmd: (1, "", ""))
    with caplog.at_level(logging.DEBUG, logger="usage"):
        refresh(store, cfg, runner=lambda cmd: (1, "", ""))
    assert not [r for r in caplog.records if r.name == "usage.parser"]
    infos = [r.getMessage() for r in caplog.records if r.name == "usage.pipeline" and r.levelno == logging.INFO]
    assert infos and "0 file(s)" in infos[0]


def test_background_refresh_failure_is_logged_with_traceback_and_survives(tmp_path, caplog, monkeypatch):
    cfg = _cfg(tmp_path)
    srv = UsageServer(cfg, Store(cfg.db_path), runner=lambda cmd: (1, "", ""), port=0)
    import usage.server as mod

    def boom(*a, **k):
        raise RuntimeError("kaput")
    monkeypatch.setattr(mod, "refresh", boom)
    with caplog.at_level(logging.ERROR, logger="usage"):
        srv._refresh_guarded()
    errors = [r for r in caplog.records if r.name == "usage.server" and r.levelno == logging.ERROR]
    assert len(errors) == 1 and "kaput" in errors[0].exc_text
    srv.httpd.server_close()
