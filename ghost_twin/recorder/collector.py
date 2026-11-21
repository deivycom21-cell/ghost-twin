"""Attach listeners to a Playwright page and turn what happens into ``Finding`` objects."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ghost_twin.models import Action, Finding
from ghost_twin.recorder import detectors as d
from ghost_twin.safety import ScopeGuard

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import ConsoleMessage, Dialog, Page, Request, Response

BODY_TYPES = {"document", "fetch", "xhr"}
TEXT_CTYPES = ("text/", "application/json", "application/xml", "application/problem+json",
               "application/javascript")
MAX_BODY = 300_000


class Collector:
    def __init__(self, guard: ScopeGuard, slow_ms: int, on_finding: Callable[[Finding], None]):
        self.guard = guard
        self.slow_ms = slow_ms
        self.on_finding = on_finding
        self.throttled = False
        self.dialogs: list[str] = []
        self.last_doc_response: Response | None = None
        self.payload: str | None = None
        self.payload_kind: str | None = None
        self.param: str | None = None
        self._posts: list[dict[str, Any]] = []
        self._post_by_req: dict[int, dict[str, Any]] = {}
        self._pending: set[asyncio.Task] = set()
        self._seen: set[tuple] = set()
        self._flagged_double: set[str] = set()

    # ------------------------------------------------------------------ lifecycle
    def attach(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", lambda err: self._on_pageerror(page, err))
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfinished", self._on_finished)
        page.on("requestfailed", self._on_failed)
        page.on("dialog", self._on_dialog)
        self._page = page

    def begin_action(self, action: Action) -> None:
        self.payload, self.payload_kind, self.param = action.payload, action.payload_kind, action.param
        self.dialogs.clear()

    def end_action(self) -> None:
        self.payload = self.payload_kind = self.param = None

    async def flush(self, timeout: float = 8.0) -> None:
        if not self._pending:
            return
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait(set(self._pending), timeout=timeout)

    # ------------------------------------------------------------------ emit
    def emit(self, f: Finding | None) -> None:
        if f is None:
            return
        path = urlsplit(f.url).path
        key = (f.kind, f.method, path, f.status, f.payload, f.param,
               f.evidence[:80] if f.kind in {"console_error", "js_exception"} else "")
        if key in self._seen:
            return
        self._seen.add(key)
        self.on_finding(f)

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _url(self) -> str:
        try:
            return self._page.url
        except Exception:
            return self.guard.target

    # ------------------------------------------------------------------ handlers
    def _on_console(self, msg: ConsoleMessage) -> None:
        self.emit(d.detect_console(msg.type, msg.text, self._url()))

    def _on_pageerror(self, page: Page, err: Any) -> None:
        message = getattr(err, "message", None) or str(err)
        stack = getattr(err, "stack", "") or ""
        self.emit(d.detect_page_error(message, page.url, stack))

    def _on_dialog(self, dialog: Dialog) -> None:
        self.dialogs.append(dialog.message)
        self._spawn(dialog.dismiss())

    def _on_request(self, req: Request) -> None:
        if req.method in {"GET", "HEAD", "OPTIONS"} or not self.guard.same_origin(req.url):
            return
        if req.resource_type not in BODY_TYPES:
            return
        body = req.post_data_buffer or b""
        rec = {"ts": time.monotonic(), "url": req.url, "method": req.method,
               "body_hash": hashlib.sha1(body).hexdigest(), "status": None}
        self._posts.append(rec)
        self._post_by_req[id(req)] = rec
        cutoff = time.monotonic() - 10
        self._posts = [p for p in self._posts if p["ts"] >= cutoff]

    def _on_response(self, resp: Response) -> None:
        req = resp.request
        if not self.guard.same_origin(resp.url):
            return
        rec = self._post_by_req.pop(id(req), None)
        if rec is not None:
            rec["status"] = resp.status
        try:
            is_main_doc = req.is_navigation_request() and req.frame.parent_frame is None
        except Exception:
            is_main_doc = False
        if is_main_doc:
            self.last_doc_response = resp
        rtype = req.resource_type
        ctype = (resp.headers.get("content-type") or "").lower()
        needs_body = rtype in BODY_TYPES and (
            resp.status >= 500 or (self.payload is not None and ctype.startswith(TEXT_CTYPES)))
        payload = (self.payload, self.payload_kind, self.param)
        if needs_body:
            self._spawn(self._analyze(resp, req.method, rtype, payload))
        else:
            self.emit(d.detect_http_status(resp.url, req.method, resp.status, rtype))

    async def _analyze(self, resp: Response, method: str, rtype: str, payload: tuple) -> None:
        value, kind, param = payload
        try:
            body = (await resp.text())[:MAX_BODY]
        except Exception:
            body = ""
        sql = d.detect_sql_leak(body, resp.url, method, resp.status, value, param) \
            if (resp.status >= 500 or kind == "sqli") else None
        if sql:
            self.emit(sql)
        elif resp.status >= 500:
            self.emit(d.detect_http_status(resp.url, method, resp.status, rtype, body, value, param))
        if kind == "traversal":
            self.emit(d.detect_traversal(body, resp.url, value, method, param, resp.status))

    def _on_finished(self, req: Request) -> None:
        if self.throttled or not self.guard.same_origin(req.url):
            return
        try:
            timing = req.timing
            elapsed = timing.get("responseEnd", -1)
        except Exception:
            return
        if elapsed and elapsed > 0 and req.resource_type in BODY_TYPES:
            self.emit(d.detect_slow(req.url, req.method, elapsed, self.slow_ms, req.resource_type))

    def _on_failed(self, req: Request) -> None:
        rec = self._post_by_req.pop(id(req), None)
        if rec is not None:
            rec["status"] = 0  # aborted: not in flight, not accepted
        if not self.guard.same_origin(req.url):
            return
        self.emit(d.detect_request_failed(req.url, req.method, req.failure or "", req.resource_type))

    # ------------------------------------------------------------------ checks
    async def wait_inflight_posts(self, timeout: float = 2.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end and any(
                p["status"] is None and time.monotonic() - p["ts"] < 5 for p in self._posts):
            await asyncio.sleep(0.05)

    def check_double_submit(self) -> None:
        """Called after every action. Records are kept ~10 s (pruned in ``_on_request``) so a
        response that lands after this check is still matched on the next one."""
        f = d.detect_double_submit(self._posts)
        if f is not None:
            k = f"{f.method}:{urlsplit(f.url).path}"
            if k not in self._flagged_double:
                self._flagged_double.add(k)
                self.emit(f)
