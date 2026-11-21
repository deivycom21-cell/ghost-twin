from pathlib import Path

from fastapi.testclient import TestClient

from ghost_twin.models import Action
from ghost_twin.recorder.store import Store
from ghost_twin.web.server import create_app
from tests.test_reporter import populate


def make(tmp_path: Path):
    store = Store.in_dir(tmp_path)
    run = populate(store)
    shot = tmp_path / "runs" / "1" / "screens" / "session-1-step-2.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG fake")
    (tmp_path / "secret.txt").write_text("top secret")
    return store, run, TestClient(create_app(tmp_path, poll_interval=0.05))


def test_runs_and_detail(tmp_path):
    _, run, client = make(tmp_path)
    assert client.get("/api/health").json()["ok"]
    runs = client.get("/api/runs").json()
    assert runs[0]["id"] == run and runs[0]["findings"] == 7
    d = client.get(f"/api/runs/{run}").json()
    assert d["run"]["status"] == "target_down"
    assert [b["severity"] for b in d["bugs"]][:2] == ["critical", "critical"]
    contact = next(b for b in d["bugs"] if "contact" in b["url"])
    assert contact["screenshot"] == "runs/1/screens/session-1-step-2.png"
    assert d["sessions"][0]["last_step"].startswith("Fill form")
    assert client.get("/api/runs/999").status_code == 404


def test_report_and_steps(tmp_path):
    _, run, client = make(tmp_path)
    r = client.get(f"/api/runs/{run}/report")
    assert r.status_code == 200 and "BUG_REPORT" in r.text
    assert "Instrucciones" in client.get(f"/api/runs/{run}/report?lang=es").text
    steps = client.get("/api/sessions/1/steps?upto=1").json()
    assert [s["idx"] for s in steps] == [0, 1]
    events = client.get(f"/api/runs/{run}/events?limit=3").json()
    assert len(events) == 3 and events[-1]["type"] == "run_end"
    assert events[0]["id"] < events[-1]["id"]


def test_index_and_static(tmp_path):
    _, _, client = make(tmp_path)
    assert "ghost-twin" in client.get("/").text
    js = client.get("/static/app.js").text
    assert "innerHTML" not in js.replace("Never use innerHTML", "")  # XSS-safe rendering


def test_artifacts_cannot_escape_runs_dir(tmp_path):
    _, _, client = make(tmp_path)
    assert client.get("/artifacts/runs/1/screens/session-1-step-2.png").status_code == 200
    for bad in ["/artifacts/secret.txt", "/artifacts/../secret.txt", "/artifacts/runs/../secret.txt",
                "/artifacts/runs/..%2F..%2Fsecret.txt", "/artifacts/ghost.db"]:
        assert client.get(bad).status_code == 404, bad


def test_websocket_streams_new_events(tmp_path):
    store, run, client = make(tmp_path)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        sid = store.create_session(run, 9, "chaos_gremlin")
        store.add_step(run, sid, 0, Action(kind="reload"))
        e1, e2 = ws.receive_json(), ws.receive_json()
        assert (e1["type"], e2["type"]) == ("session_start", "step")
        assert e2["data"]["description"] == "Reload the page (F5)"
