import http.client
import json
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from tests.helpers import assistant_msg, iso, make_session, user_msg
from usage.config import load_config
from usage.server import UsageServer
from usage.store import Store


def test_server_serves_summary_and_static(tmp_path):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                                "ccusage_command": ["ccusage"], "refresh_seconds": 3600}))
    cfg = load_config(cfgp)
    make_session(tmp_path, "-p", "s1", [user_msg("hi", iso(0)), assistant_msg("m1", iso(1), cache_read=10)])
    srv = UsageServer(cfg, Store(cfg.db_path), runner=lambda cmd: (1, "", "none"), port=0)
    srv.refresh_once()
    srv.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/summary" % srv.port, timeout=5) as r:
            data = json.loads(r.read())
        assert r.status == 200
        assert data["cost_available"] is False and len(data["sessions"]) == 1
        day = data["days"][0]
        with urllib.request.urlopen("http://127.0.0.1:%d/api/summary?period=%s" % (srv.port, day), timeout=5) as r:
            by_day = json.loads(r.read())
        assert by_day["kind"] == "day" and by_day["period"] == day and len(by_day["series"]) == 24
        with urllib.request.urlopen("http://127.0.0.1:%d/api/summary?month=%s" % (srv.port, day[:7]), timeout=5) as r:
            assert json.loads(r.read())["period"] == day[:7]           # the old parameter still works
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        conn.request("GET", "/api/summary?period=2026-9")
        resp = conn.getresponse()
        assert resp.status == 400 and "period" in json.loads(resp.read())["error"]
        conn.close()
        with urllib.request.urlopen("http://127.0.0.1:%d/api/office" % srv.port, timeout=5) as r:
            office = json.loads(r.read())
        assert office["rooms"] == [] and office["working_seconds"] == 60
        # the notice board on the plaza carries today's real numbers (spec Sec 12.7)
        assert office["board"]["day"] == date.today().isoformat()
        assert office["board"]["unit"] == "tokens" and office["board"]["value"] == 0
        with urllib.request.urlopen("http://127.0.0.1:%d/" % srv.port, timeout=5) as r:
            body = r.read().decode()
        assert "<html" in body.lower()
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        conn.request("GET", "/../../../../../../etc/passwd")
        resp = conn.getresponse()
        assert resp.status == 404
        assert json.loads(resp.read())["error"]
        conn.close()
    finally:
        srv.stop()


def test_server_returns_500_json_on_error(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                                "ccusage_command": ["ccusage"]}))
    cfg = load_config(cfgp)
    srv = UsageServer(cfg, Store(cfg.db_path), runner=lambda cmd: (1, "", ""), port=0)
    import usage.server as mod

    def boom(*a, **k):
        raise RuntimeError("kaput")
    monkeypatch.setattr(mod, "build_summary", boom)
    srv.start()
    try:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/summary" % srv.port, timeout=5)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 500
            assert json.loads(e.read())["error"] == "kaput"
    finally:
        srv.stop()


def test_server_refuses_a_foreign_host_header(tmp_path):
    """DNS rebinding: a page on evil.example rebound to 127.0.0.1 must not
    read session titles and messages through the browser."""
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"claude_dir": str(tmp_path), "db_path": str(tmp_path / "u.db"),
                                "ccusage_command": ["ccusage"], "refresh_seconds": 3600}))
    cfg = load_config(cfgp)
    srv = UsageServer(cfg, Store(cfg.db_path), runner=lambda cmd: (1, "", "none"), port=0)
    srv.start()
    try:
        def status(host):
            conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
            conn.request("GET", "/api/office", headers={"Host": host})
            code = conn.getresponse().status
            conn.close()
            return code
        assert status("evil.example:%d" % srv.port) == 403
        assert status("127.0.0.1.evil.example:%d" % srv.port) == 403
        assert status("127.0.0.1:%d" % srv.port) == 200
        assert status("localhost:%d" % srv.port) == 200
        assert status("LocalHost.:%d" % srv.port) == 200          # case-insensitive, root dot allowed
        assert status("localhost..:%d" % srv.port) == 403
        assert status("localhost:%d" % (srv.port + 1)) == 403
        assert status("localhost") == 403
    finally:
        srv.stop()
