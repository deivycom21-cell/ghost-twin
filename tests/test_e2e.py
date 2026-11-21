"""End-to-end: real headless Chromium against the buggy demo app (auto-skipped without it)."""

import asyncio
import threading

import pytest

from ghost_twin.config import RunConfig
from ghost_twin.engine.orchestrator import Orchestrator
from ghost_twin.recorder.store import Store

pytestmark = pytest.mark.e2e


def cfg_for(url, tmp_path, **kw):
    base = dict(target=url, users=1, duration=90, max_browsers=1, data_dir=tmp_path,
                slow_ms=1000, step_delay_ms=30, api=False, video="off", health_interval=0.5)
    base.update(kw)
    return RunConfig(**base)


async def attack(cfg):
    store = Store.in_dir(cfg.data_dir)
    orch = Orchestrator(cfg, store)
    result = await orch.run()
    kinds = {f["kind"] for f in store.findings(result.run_id)}
    return result, store, kinds


async def test_collector_page_level_signals(buggy_app, tmp_path):
    result, _, kinds = await attack(cfg_for(buggy_app.url, tmp_path, personas=["normal_user"],
                                            max_steps=1))
    assert "console_error" in kinds  # home page logs an error on load
    _, _, kinds = await attack(cfg_for(buggy_app.url + "/broken-js", tmp_path,
                                       personas=["normal_user"], max_steps=1))
    assert "js_exception" in kinds
    _, _, kinds = await attack(cfg_for(buggy_app.url + "/slow", tmp_path,
                                       personas=["normal_user"], max_steps=1))
    assert "slow_response" in kinds


async def test_normal_user_finds_browsing_bugs(buggy_app, tmp_path):
    result, store, kinds = await attack(cfg_for(buggy_app.url, tmp_path, personas=["normal_user"],
                                                max_steps=14))
    assert {"console_error", "js_exception", "http_5xx", "slow_response"} <= kinds
    assert result.status == "finished"
    steps = store.steps(store.sessions(result.run_id)[0]["id"])
    assert len(steps) == 14 and steps[0]["kind"] == "goto"


async def test_chaos_gremlin_finds_double_submit(buggy_app, tmp_path):
    _, _, kinds = await attack(cfg_for(buggy_app.url + "/signup", tmp_path,
                                       personas=["chaos_gremlin"], max_steps=4))
    assert "double_submit" in kinds
    assert len(buggy_app.signups) >= 2


async def test_chaos_gremlin_breaks_contact_form(buggy_app, tmp_path):
    _, store, kinds = await attack(cfg_for(buggy_app.url + "/contact", tmp_path,
                                           personas=["chaos_gremlin"], max_steps=12))
    assert "http_5xx" in kinds


async def test_security_prober_finds_injections(buggy_app, tmp_path):
    _, store, kinds = await attack(cfg_for(buggy_app.url, tmp_path, personas=["security_prober"],
                                           max_steps=12))
    assert {"sql_error_leak", "reflected_xss", "path_traversal"} <= kinds
    xss = next(f for f in store.findings(1) if f["kind"] == "reflected_xss")
    assert xss["param"] == "q" and "gtx" in xss["payload"]


async def test_same_seed_same_steps(buggy_app, tmp_path):
    runs = []
    for i in range(2):
        result, store, _ = await attack(cfg_for(buggy_app.url, tmp_path / str(i),
                                                personas=["chaos_gremlin"], max_steps=8,
                                                seed=99))
        sid = store.sessions(result.run_id)[0]["id"]
        runs.append([s["description"] for s in store.steps(sid)])
    assert runs[0] == runs[1]


async def test_max_browsers_queues_agents(buggy_app, tmp_path):
    result, store, _ = await attack(cfg_for(buggy_app.url, tmp_path, users=3, max_browsers=1,
                                            max_steps=3))
    sessions = store.sessions(result.run_id)
    assert len(sessions) == 3
    assert {s["persona"] for s in sessions} == {"normal_user", "chaos_gremlin", "security_prober"}
    # one context at a time: each session starts after the previous one finished
    for a, b in zip(sessions, sessions[1:], strict=False):
        assert b["started_at"] >= a["finished_at"]


async def test_artifacts_only_for_failing_sessions(buggy_app, tmp_path):
    clean, store, kinds = await attack(cfg_for(buggy_app.url + "/about", tmp_path,
                                               personas=["normal_user"], max_steps=1,
                                               video="on-failure"))
    assert not kinds
    assert store.artifacts(clean.run_id) == []
    assert not list((tmp_path / "runs" / str(clean.run_id)).rglob("*.webm"))

    dirty, store, kinds = await attack(cfg_for(buggy_app.url + "/broken-js", tmp_path,
                                               personas=["normal_user"], max_steps=1,
                                               video="on-failure"))
    arts = store.artifacts(dirty.run_id)
    assert {a["kind"] for a in arts} == {"screenshot", "video"}
    for a in arts:
        assert (tmp_path / a["path"]).stat().st_size > 0
    assert not (tmp_path / "runs" / str(dirty.run_id) / "_tmp_video").exists()


async def test_server_crash_is_detected(buggy_app, tmp_path):
    timer = threading.Timer(2.0, buggy_app.crash)
    timer.start()
    try:
        result, store, kinds = await attack(cfg_for(buggy_app.url, tmp_path,
                                                    personas=["normal_user"], max_steps=200,
                                                    duration=40))
    finally:
        timer.cancel()
    assert "server_crash" in kinds
    assert result.status == "target_down"
    assert result.elapsed < 30
    crash = next(f for f in store.findings(result.run_id) if f["kind"] == "server_crash")
    assert "Last actions" in crash["evidence"]


async def test_api_agent_runs_alongside(buggy_app, tmp_path):
    result, store, kinds = await attack(cfg_for(buggy_app.url + "/about", tmp_path,
                                                personas=["normal_user"], max_steps=2, api=True))
    personas = {s["persona"] for s in store.sessions(result.run_id)}
    assert "api_fuzzer" in personas and "http_5xx" in kinds


def test_cli_attack_end_to_end(buggy_app, tmp_path):
    from typer.testing import CliRunner

    from ghost_twin.cli import app

    out = tmp_path / "BUG_REPORT.md"
    r = CliRunner().invoke(app, [
        "attack", buggy_app.url, "--users", "3", "--max-steps", "8", "--max-browsers", "3",
        "--data-dir", str(tmp_path / "d"), "--report", str(out), "--no-video", "--fail-on", "high"])
    assert r.exit_code == 1, r.output  # bugs >= high exist -> CI failure
    text = out.read_text(encoding="utf-8")
    assert "Instructions for the coding agent" in text and "BUG-001" in text


async def test_interrupt_still_finishes_run(buggy_app, tmp_path):
    cfg = cfg_for(buggy_app.url, tmp_path, personas=["normal_user"], max_steps=500, duration=60)
    store = Store.in_dir(tmp_path)
    orch = Orchestrator(cfg, store)
    task = asyncio.create_task(orch.run())
    await asyncio.sleep(4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get_run(orch.run_id)["status"] == "interrupted"
