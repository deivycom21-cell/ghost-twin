"""SQLite persistence (WAL). One ``Store`` per thread/process; writes are lock-protected."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ghost_twin.models import Action, Finding

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'running',
    seed INTEGER,
    config_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    agent_index INTEGER NOT NULL,
    persona TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at REAL NOT NULL,
    finished_at REAL,
    steps INTEGER NOT NULL DEFAULT 0,
    video_path TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    idx INTEGER NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    description TEXT NOT NULL,
    url TEXT,
    action_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    session_id INTEGER REFERENCES sessions(id),
    step_idx INTEGER,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    method TEXT,
    status INTEGER,
    payload TEXT,
    param TEXT,
    evidence TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    step_idx INTEGER,
    kind TEXT NOT NULL,
    path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    session_id INTEGER,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ai_cache (
    key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_steps_session ON steps(session_id, idx);
"""


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


class Store:
    def __init__(self, path: str | Path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @classmethod
    def in_dir(cls, data_dir: str | Path) -> Store:
        return cls(Path(data_dir) / "ghost.db")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ internals
    def _exec(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._conn.execute(sql, params).fetchone())

    # ------------------------------------------------------------------ events
    def add_event(self, run_id: int, type_: str, data: dict[str, Any] | None = None,
                  session_id: int | None = None) -> int:
        return self._exec(
            "INSERT INTO events (run_id, session_id, ts, type, data_json) VALUES (?,?,?,?,?)",
            (run_id, session_id, self._clock(), type_, json.dumps(data or {}, default=str)),
        )

    def events_since(self, last_id: int = 0, run_id: int | None = None,
                     limit: int = 500) -> list[dict[str, Any]]:
        if run_id is None:
            rows = self._all("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (last_id, limit))
        else:
            rows = self._all(
                "SELECT * FROM events WHERE id > ? AND run_id = ? ORDER BY id LIMIT ?",
                (last_id, run_id, limit),
            )
        for r in rows:
            r["data"] = json.loads(r.pop("data_json") or "{}")
        return rows

    def recent_events(self, run_id: int, limit: int = 150) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                         (run_id, limit))
        for r in rows:
            r["data"] = json.loads(r.pop("data_json") or "{}")
        return list(reversed(rows))

    def last_event_id(self) -> int:
        r = self._one("SELECT MAX(id) AS m FROM events")
        return int(r["m"] or 0) if r else 0

    # ------------------------------------------------------------------ runs
    def create_run(self, target: str, config: dict[str, Any] | None = None,
                   seed: int | None = None) -> int:
        run_id = self._exec(
            "INSERT INTO runs (target, started_at, seed, config_json) VALUES (?,?,?,?)",
            (target, self._clock(), seed, json.dumps(config or {}, default=str)),
        )
        self.add_event(run_id, "run_start", {"target": target, "config": config or {}})
        return run_id

    def finish_run(self, run_id: int, status: str = "finished") -> None:
        self._exec("UPDATE runs SET finished_at=?, status=? WHERE id=?", (self._clock(), status, run_id))
        n = self._one("SELECT COUNT(*) AS n FROM findings WHERE run_id=?", (run_id,))
        self.add_event(run_id, "run_end", {"status": status, "findings": n["n"] if n else 0})

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM runs WHERE id=?", (run_id,))
        if r:
            r["config"] = json.loads(r.pop("config_json") or "{}")
        return r

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT r.id, r.target, r.started_at, r.finished_at, r.status, r.seed,
                      (SELECT COUNT(*) FROM findings f WHERE f.run_id=r.id) AS findings,
                      (SELECT COUNT(*) FROM sessions s WHERE s.run_id=r.id) AS sessions
               FROM runs r ORDER BY r.id DESC LIMIT ?""",
            (limit,),
        )
        return rows

    def latest_run_id(self) -> int | None:
        r = self._one("SELECT MAX(id) AS m FROM runs")
        return int(r["m"]) if r and r["m"] is not None else None

    # ------------------------------------------------------------------ sessions
    def create_session(self, run_id: int, agent_index: int, persona: str) -> int:
        sid = self._exec(
            "INSERT INTO sessions (run_id, agent_index, persona, started_at) VALUES (?,?,?,?)",
            (run_id, agent_index, persona, self._clock()),
        )
        self.add_event(run_id, "session_start",
                       {"session_id": sid, "agent_index": agent_index, "persona": persona}, sid)
        return sid

    def finish_session(self, session_id: int, status: str, video_path: str | None = None) -> None:
        self._exec(
            "UPDATE sessions SET status=?, finished_at=?, video_path=? WHERE id=?",
            (status, self._clock(), video_path, session_id),
        )
        s = self.get_session(session_id)
        if s:
            n = self._one("SELECT COUNT(*) AS n FROM findings WHERE session_id=?", (session_id,))
            self.add_event(s["run_id"], "session_end", {
                "session_id": session_id, "status": status, "video": video_path,
                "findings": n["n"] if n else 0, "steps": s["steps"],
            }, session_id)

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM sessions WHERE id=?", (session_id,))

    def sessions(self, run_id: int) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM sessions WHERE run_id=? ORDER BY id", (run_id,))

    # ------------------------------------------------------------------ steps
    def add_step(self, run_id: int, session_id: int, idx: int, action: Action,
                 url: str | None = None, persona: str | None = None) -> int:
        desc = action.describe()
        sid = self._exec(
            "INSERT INTO steps (session_id, idx, ts, kind, description, url, action_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (session_id, idx, self._clock(), action.kind, desc, url,
             json.dumps({"kind": action.kind, "target": action.target, "payload": action.payload,
                         "param": action.param, "mode": action.mode})),
        )
        self._exec("UPDATE sessions SET steps=? WHERE id=?", (idx + 1, session_id))
        self.add_event(run_id, "step", {"session_id": session_id, "idx": idx, "kind": action.kind,
                                        "description": desc, "url": url, "persona": persona},
                       session_id)
        return sid

    def steps(self, session_id: int, upto: int | None = None) -> list[dict[str, Any]]:
        if upto is None:
            return self._all("SELECT * FROM steps WHERE session_id=? ORDER BY idx", (session_id,))
        return self._all("SELECT * FROM steps WHERE session_id=? AND idx<=? ORDER BY idx",
                         (session_id, upto))

    def recent_steps(self, run_id: int, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT st.*, se.persona FROM steps st JOIN sessions se ON se.id = st.session_id
               WHERE se.run_id=? ORDER BY st.id DESC LIMIT ?""",
            (run_id, limit),
        )
        return list(reversed(rows))

    # ------------------------------------------------------------------ findings
    def add_finding(self, run_id: int, finding: Finding, session_id: int | None = None,
                    step_idx: int | None = None, persona: str | None = None) -> int:
        fid = self._exec(
            """INSERT INTO findings (run_id, session_id, step_idx, ts, kind, severity, title, url,
                   method, status, payload, param, evidence, detail_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, session_id, step_idx, self._clock(), finding.kind, finding.severity.value,
             finding.title, finding.url, finding.method, finding.status, finding.payload,
             finding.param, finding.evidence, json.dumps(finding.detail, default=str)),
        )
        self.add_event(run_id, "finding", {
            "id": fid, "session_id": session_id, "kind": finding.kind,
            "severity": finding.severity.value, "title": finding.title, "url": finding.url,
            "persona": persona, "step_idx": step_idx,
        }, session_id)
        return fid

    def findings(self, run_id: int) -> list[dict[str, Any]]:
        rows = self._all(
            """SELECT f.*, s.persona FROM findings f LEFT JOIN sessions s ON s.id=f.session_id
               WHERE f.run_id=? ORDER BY f.id""",
            (run_id,),
        )
        for r in rows:
            r["detail"] = json.loads(r.pop("detail_json") or "{}")
        return rows

    # ------------------------------------------------------------------ artifacts
    def add_artifact(self, session_id: int, kind: str, path: str, step_idx: int | None = None) -> int:
        return self._exec(
            "INSERT INTO artifacts (session_id, step_idx, kind, path) VALUES (?,?,?,?)",
            (session_id, step_idx, kind, path),
        )

    def artifacts(self, run_id: int) -> list[dict[str, Any]]:
        return self._all(
            """SELECT a.* FROM artifacts a JOIN sessions s ON s.id=a.session_id
               WHERE s.run_id=? ORDER BY a.id""",
            (run_id,),
        )

    # ------------------------------------------------------------------ ai cache
    def cache_get(self, key: str) -> str | None:
        r = self._one("SELECT response FROM ai_cache WHERE key=?", (key,))
        return r["response"] if r else None

    def cache_set(self, key: str, response: str) -> None:
        self._exec("INSERT OR REPLACE INTO ai_cache (key, response, created_at) VALUES (?,?,?)",
                   (key, response, self._clock()))
