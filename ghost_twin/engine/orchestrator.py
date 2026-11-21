"""Runs an attack: N ghost agents (bounded by --max-browsers), API fuzzing and health checks."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from ghost_twin.config import RunConfig
from ghost_twin.engine.browser import launch_browser, new_context
from ghost_twin.engine.health import check_reachable, health_monitor
from ghost_twin.models import Finding, Severity
from ghost_twin.personas.base import AgentContext
from ghost_twin.personas.registry import assign_personas, create_persona
from ghost_twin.recorder.artifacts import ArtifactManager
from ghost_twin.recorder.store import Store
from ghost_twin.safety import ScopeGuard

Listener = Callable[[str, dict[str, Any]], None]


class TargetUnreachable(RuntimeError):
    pass


@dataclass
class AttackResult:
    run_id: int
    status: str
    findings: int
    sessions: int
    elapsed: float


class Orchestrator:
    def __init__(self, cfg: RunConfig, store: Store | None = None, listener: Listener | None = None,
                 ai: Any = None):
        self.cfg = cfg.validate()
        self.guard = ScopeGuard(cfg.target, cfg.allow_remote)
        self.store = store or Store.in_dir(cfg.data_dir)
        self.listener = listener or (lambda _t, _d: None)
        self.ai = ai
        self.stop_event = asyncio.Event()
        self.run_id: int | None = None
        self.status = "finished"
        self.findings = 0

    # ------------------------------------------------------------------ callbacks
    def _emit(self, type_: str, data: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            self.listener(type_, data)

    def _on_finding(self, f: Finding, persona: str) -> None:
        self.findings += 1
        self._emit("finding", {"persona": persona, **f.to_dict()})

    async def _on_target_down(self, error: str) -> None:
        if self.stop_event.is_set():
            return
        recent = self.store.recent_steps(self.run_id, 12)
        evidence = "Target stopped accepting connections (" + error + ").\n\nLast actions before " \
                   "the crash:\n" + "\n".join(
                       f"- [{s['persona']} #{s['session_id']}] {s['description']}" for s in recent)
        f = Finding(kind="server_crash", severity=Severity.CRITICAL,
                    title="Target app crashed / stopped responding during the attack",
                    url=self.guard.target, evidence=evidence,
                    detail={"recent_steps": [s["description"] for s in recent]})
        self.store.add_finding(self.run_id, f, None, None, "health_check")
        self._on_finding(f, "health_check")
        self.status = "target_down"
        self.stop_event.set()

    # ------------------------------------------------------------------ agents
    async def _agent(self, browser, index: int, persona_name: str, sem: asyncio.Semaphore,
                     slice_s: float, global_deadline: float, artifacts: ArtifactManager) -> None:
        from ghost_twin.engine.agent import AgentRunner

        async with sem:
            if self.stop_event.is_set() or time.monotonic() >= global_deadline:
                return
            deadline = min(global_deadline, time.monotonic() + slice_s)
            sid = self.store.create_session(self.run_id, index, persona_name)
            self._emit("session_start", {"session_id": sid, "persona": persona_name, "index": index})
            video_dir = artifacts.video_tmp_dir(sid) if self.cfg.video == "on-failure" else None
            context = await new_context(browser, self.cfg, video_dir)
            ctx = AgentContext(start_url=self.guard.target, guard=self.guard,
                               seed=self.cfg.seed * 1000 + index,
                               allow_destructive=self.cfg.allow_destructive,
                               aggressive=self.cfg.aggressive, ai=self.ai)
            runner = AgentRunner(persona=create_persona(persona_name, ctx), context=context,
                                 store=self.store, run_id=self.run_id, session_id=sid, cfg=self.cfg,
                                 guard=self.guard, artifacts=artifacts, stop_event=self.stop_event,
                                 on_finding=self._on_finding)
            status = "error"
            try:
                status = await runner.run(deadline, self.cfg.max_steps)
            finally:
                videos = await runner.close()
                video_rel = None
                if runner.findings and videos:
                    kept = artifacts.keep_videos(sid, videos)
                    for p in kept:
                        self.store.add_artifact(sid, "video", artifacts.rel(p))
                    video_rel = artifacts.rel(kept[0]) if kept else None
                else:
                    artifacts.discard(videos)
                self.store.finish_session(sid, status, video_rel)
                self._emit("session_end", {"session_id": sid, "persona": persona_name,
                                           "status": status, "findings": runner.findings,
                                           "steps": runner.step_idx + 1})

    async def _api_agent(self, index: int, deadline: float) -> None:
        from ghost_twin.api.openapi_fuzzer import discover_spec, run_api_fuzz

        p = urlsplit(self.guard.target)
        origin = f"{p.scheme}://{p.netloc}"
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            spec, spec_url = await discover_spec(client, origin, self.cfg.openapi)
            if not spec and p.path.strip("/"):
                spec, spec_url = await discover_spec(client, self.guard.target)
        if not spec:
            return
        base = origin
        servers = spec.get("servers") or []
        if servers and isinstance(servers[0], dict) and str(servers[0].get("url", "")).startswith("/"):
            base = origin + servers[0]["url"].rstrip("/")
        sid = self.store.create_session(self.run_id, index, "api_fuzzer")
        self._emit("session_start", {"session_id": sid, "persona": "api_fuzzer", "index": index,
                                     "spec": spec_url})
        count = {"findings": 0, "steps": 0}

        def record_step(idx, action):
            count["steps"] = idx + 1
            self.store.add_step(self.run_id, sid, idx, action, url=action.target.split(" ", 1)[-1],
                                persona="api_fuzzer")

        def record_finding(idx, f):
            count["findings"] += 1
            self.store.add_finding(self.run_id, f, sid, idx, "api_fuzzer")
            self._on_finding(f, "api_fuzzer")

        status = "ok"
        try:
            await run_api_fuzz(base=base, guard=self.guard, spec=spec,
                               record_step=record_step, record_finding=record_finding,
                               deadline=deadline, stop_event=self.stop_event,
                               max_requests=self.cfg.max_api_requests, slow_ms=self.cfg.slow_ms,
                               allow_destructive=self.cfg.allow_destructive,
                               aggressive=self.cfg.aggressive)
        except Exception as exc:
            status = "error"
            self.store.add_event(self.run_id, "log", {"level": "error",
                                                      "message": f"api fuzzer crashed: {exc!r}"}, sid)
        if status == "ok" and count["findings"]:
            status = "failed"
        self.store.finish_session(sid, status)
        self._emit("session_end", {"session_id": sid, "persona": "api_fuzzer", "status": status,
                                   "findings": count["findings"], "steps": count["steps"]})

    # ------------------------------------------------------------------ main
    async def run(self) -> AttackResult:
        from playwright.async_api import async_playwright

        cfg = self.cfg
        self.guard.check_target()
        ok, detail = await check_reachable(cfg.target)
        if not ok:
            raise TargetUnreachable(f"{cfg.target} is not reachable ({detail}). Is your app running?")

        self.run_id = self.store.create_run(cfg.target, cfg.to_dict(), cfg.seed)
        self._emit("run_start", {"run_id": self.run_id, "target": cfg.target})
        artifacts = ArtifactManager(cfg.data_dir, self.run_id)
        started = time.monotonic()
        global_deadline = started + cfg.duration
        rounds = math.ceil(cfg.users / cfg.max_browsers)
        slice_s = cfg.duration / rounds
        names = assign_personas(cfg.users, cfg.personas)
        health = asyncio.create_task(health_monitor(cfg.target, cfg.health_interval,
                                                    self.stop_event, self._on_target_down))
        try:
            async with async_playwright() as pw:
                browser = await launch_browser(pw, cfg.headless)
                sem = asyncio.Semaphore(cfg.max_browsers)
                tasks = [self._agent(browser, i, n, sem, slice_s, global_deadline, artifacts)
                         for i, n in enumerate(names)]
                if cfg.api:
                    tasks.append(self._api_agent(len(names), global_deadline))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                # Agents may finish before the periodic health-check notices a dead target.
                if not self.stop_event.is_set():
                    alive, detail = await check_reachable(cfg.target, timeout=5)
                    if not alive:
                        await self._on_target_down(detail)
                for r in results:
                    if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                        self.store.add_event(self.run_id, "log",
                                             {"level": "error", "message": f"agent failed: {r!r}"})
                with contextlib.suppress(Exception):
                    await browser.close()
        except (asyncio.CancelledError, KeyboardInterrupt):
            self.status = "interrupted"
            raise
        except Exception:
            self.status = "error"
            raise
        finally:
            self.stop_event.set()
            health.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await health
            artifacts.cleanup_tmp()
            self.store.finish_run(self.run_id, self.status)
            self._emit("run_end", {"run_id": self.run_id, "status": self.status,
                                   "findings": self.findings})
        return AttackResult(self.run_id, self.status, self.findings,
                            len(self.store.sessions(self.run_id)), time.monotonic() - started)


async def run_attack(cfg: RunConfig, store: Store | None = None, listener: Listener | None = None,
                     ai: Any = None) -> AttackResult:
    return await Orchestrator(cfg, store, listener, ai).run()
