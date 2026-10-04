import json
import urllib.request

from zdte.dashboard.server import DashboardServer, file_state_provider


def test_dashboard_serves_page_and_state(tmp_path):
    state = {"total_pnl": 12.5, "leaderboard": []}
    srv = DashboardServer(lambda: state, host="127.0.0.1", port=0).start()
    try:
        base = f"http://127.0.0.1:{srv.port}"
        html = urllib.request.urlopen(base + "/").read().decode()
        assert "<title>0DTE Bot Desk</title>" in html
        city = urllib.request.urlopen(base + "/city").read().decode()
        assert "<title>0DTE Bot City</title>" in city
        data = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert data["total_pnl"] == 12.5
        try:
            urllib.request.urlopen(base + "/nope")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.stop()


def test_file_state_provider(tmp_path):
    p = tmp_path / "state.json"
    assert "not found" in file_state_provider(p)()["error"]
    p.write_text(json.dumps({"x": 1}))
    assert file_state_provider(p)() == {"x": 1}


def test_export_snapshot_embeds_state(tmp_path):
    from zdte.dashboard.export import export_snapshot
    state = {"total_pnl": 42.0, "leaderboard": [], "scanner": [], "note": "</script><b>x</b>"}
    out = export_snapshot(state, tmp_path / "out", fragment_pages=("city",))
    desk = out["desk"].read_text()
    city = out["city"].read_text()
    assert desk.startswith("<!doctype html>") and "window.__ZDTE_STATE__" in desk
    assert "<\\/script>" in desk and "</script><b>" not in desk.split("__ZDTE_STATE__")[1][:200]
    assert desk.index("__ZDTE_STATE__") < desk.index("const EMBEDDED")
    assert not city.lstrip().startswith("<!doctype") and "<body" not in city and "<title>0DTE Bot City</title>" in city
    assert '"city": "city.html"' in desk
