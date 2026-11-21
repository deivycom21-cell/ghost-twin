from ghost_twin.models import Action, Finding, Severity
from ghost_twin.recorder.store import Store


def test_full_lifecycle(store):
    run = store.create_run("http://localhost:3000", {"users": 2}, seed=7)
    sid = store.create_session(run, 0, "normal_user")
    store.add_step(run, sid, 0, Action(kind="goto", target="http://localhost:3000"), url="about:blank")
    store.add_step(run, sid, 1, Action(kind="click", target="#a", label="About"), url="http://localhost:3000/")
    fid = store.add_finding(run, Finding("http_5xx", Severity.HIGH, "boom", "http://localhost:3000/x",
                                         status=500, detail={"a": 1}), sid, 1, "normal_user")
    store.add_artifact(sid, "screenshot", "runs/1/screens/s.png", 1)
    store.finish_session(sid, "failed", "runs/1/videos/session-1.webm")
    store.finish_run(run)

    r = store.get_run(run)
    assert r["status"] == "finished" and r["seed"] == 7 and r["config"] == {"users": 2}
    assert [s["description"] for s in store.steps(sid)][1] == 'Click "About"'
    assert store.steps(sid, upto=0)[0]["kind"] == "goto"
    f = store.findings(run)[0]
    assert f["id"] == fid and f["detail"] == {"a": 1} and f["persona"] == "normal_user"
    assert store.sessions(run)[0]["steps"] == 2
    assert store.list_runs()[0]["findings"] == 1
    assert store.artifacts(run)[0]["kind"] == "screenshot"
    assert store.latest_run_id() == run


def test_events_are_ordered_and_tailable(store):
    run = store.create_run("http://localhost")
    sid = store.create_session(run, 0, "chaos_gremlin")
    first = store.events_since(0)
    assert [e["type"] for e in first] == ["run_start", "session_start"]
    last = first[-1]["id"]
    store.add_step(run, sid, 0, Action(kind="reload"))
    new = store.events_since(last)
    assert len(new) == 1 and new[0]["type"] == "step"
    assert new[0]["data"]["description"] == "Reload the page (F5)"
    assert store.events_since(0, run_id=run + 99) == []


def test_reopen_keeps_data(tmp_path):
    s = Store(tmp_path / "g.db")
    run = s.create_run("http://localhost")
    s.cache_set("k", "v")
    s.close()
    s2 = Store(tmp_path / "g.db")
    assert s2.get_run(run)["target"] == "http://localhost"
    assert s2.cache_get("k") == "v" and s2.cache_get("missing") is None
    s2.close()
