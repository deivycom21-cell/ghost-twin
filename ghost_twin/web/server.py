"""Live dashboard: FastAPI + WebSocket that tails the SQLite ``events`` table.

Works both in-process (``attack --dashboard``) and as a separate process (``ghost-twin
dashboard``) because SQLite is the only channel between the attack and the UI.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ghost_twin import __version__
from ghost_twin.recorder.store import Store
from ghost_twin.reporter.dedupe import group_findings
from ghost_twin.reporter.hints import hint_for
from ghost_twin.reporter.markdown import build_report

STATIC = Path(__file__).parent / "static"


def _bug_payload(bugs, artifacts: list[dict]) -> list[dict[str, Any]]:
    out = []
    for i, b in enumerate(bugs, 1):
        f = b.first
        mine = [a for a in artifacts if a["session_id"] == f.get("session_id")]
        shots = [a for a in mine if a["kind"] == "screenshot"]
        step = f.get("step_idx") or 0
        shot = next((a for a in shots if (a["step_idx"] or 0) >= step), shots[-1] if shots else None)
        out.append({
            "n": i, "id": f"BUG-{i:03d}", "severity": b.severity.value, "kind": f["kind"],
            "title": f["title"], "url": f["url"], "method": f.get("method"),
            "status": f.get("status"), "payload": f.get("payload"), "param": f.get("param"),
            "evidence": f.get("evidence"), "hits": len(b.occurrences), "personas": b.personas,
            "session_id": f.get("session_id"), "step_idx": f.get("step_idx"),
            "curl": (f.get("detail") or {}).get("curl"), "hint": hint_for(f["kind"]),
            "screenshot": shot["path"] if shot else None,
            "videos": [a["path"] for a in mine if a["kind"] == "video"],
        })
    return out


def create_app(data_dir: str | Path, poll_interval: float = 0.4) -> FastAPI:
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store.in_dir(data_dir)
    app = FastAPI(title="ghost-twin dashboard", version=__version__, docs_url=None,
                  redoc_url=None, openapi_url=None)
    app.state.store = store
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__, "last_event_id": store.last_event_id()}

    @app.get("/api/runs")
    def runs() -> list[dict]:
        return store.list_runs()

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: int) -> dict:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        findings = store.findings(run_id)
        artifacts = store.artifacts(run_id)
        sessions = store.sessions(run_id)
        for s in sessions:
            last = store.steps(s["id"])[-1:] or [None]
            s["last_step"] = last[0]["description"] if last[0] else None
            s["findings"] = sum(1 for f in findings if f["session_id"] == s["id"])
        return {"run": run, "sessions": sessions,
                "bugs": _bug_payload(group_findings(findings), artifacts),
                "raw_findings": len(findings), "last_event_id": store.last_event_id()}

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: int, limit: int = 150) -> list[dict]:
        return store.recent_events(run_id, min(max(limit, 1), 1000))

    @app.get("/api/sessions/{session_id}/steps")
    def session_steps(session_id: int, upto: int | None = None) -> list[dict]:
        return store.steps(session_id, upto)

    @app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
    def report(run_id: int, lang: str = "en") -> str:
        if store.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        return build_report(store, run_id, data_dir=data_dir, report_dir=data_dir, lang=lang)

    @app.get("/artifacts/{path:path}", include_in_schema=False)
    def artifact(path: str) -> FileResponse:
        runs_root = (data_dir / "runs").resolve()
        target = (data_dir / path).resolve()
        if not target.is_relative_to(runs_root) or not target.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(target)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, since: int | None = None) -> None:
        await websocket.accept()
        last = store.last_event_id() if since is None else since
        await websocket.send_json({"type": "hello", "last_event_id": last, "version": __version__})
        try:
            while True:
                events = await asyncio.to_thread(store.events_since, last, None, 500)
                for e in events:
                    last = e["id"]
                    await websocket.send_json(e)
                if not events:
                    await asyncio.sleep(poll_interval)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


class DashboardThread:
    """Runs uvicorn in a daemon thread (used by ``attack --dashboard``)."""

    def __init__(self, data_dir: str | Path, host: str = "127.0.0.1", port: int = 8585):
        import uvicorn

        self.url = f"http://{host}:{port}"
        config = uvicorn.Config(create_app(data_dir), host=host, port=port, log_level="warning",
                                lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> DashboardThread:
        self.thread.start()
        for _ in range(50):
            if self.server.started:
                break
            threading.Event().wait(0.1)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        with contextlib.suppress(Exception):
            self.thread.join(timeout=3)


def serve(data_dir: str | Path, host: str = "127.0.0.1", port: int = 8585) -> None:
    import uvicorn

    uvicorn.run(create_app(data_dir), host=host, port=port, log_level="warning")
