"""AgentRunner: drives one persona inside one browser context and records everything."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ghost_twin.config import RunConfig
from ghost_twin.models import Action, FieldValue, Finding, PageSnapshot
from ghost_twin.personas.base import Persona
from ghost_twin.personas.snapshot import take_snapshot
from ghost_twin.recorder.artifacts import ArtifactManager
from ghost_twin.recorder.collector import Collector
from ghost_twin.recorder.store import Store
from ghost_twin.safety import ScopeGuard

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import BrowserContext, Page

BYPASS_JS = """(el) => {
  const root = el.tagName === 'FORM' ? el : null;
  if (root) root.noValidate = true;
  const fields = root ? root.querySelectorAll('input,textarea,select') : [el];
  for (const f of fields) {
    for (const a of ['maxlength', 'required', 'pattern', 'min', 'max', 'minlength'])
      f.removeAttribute(a);
  }
}"""
SET_VALUE_JS = """(el, v) => {
  el.value = v;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
}"""
MAX_SCREENSHOTS = 5


class AgentRunner:
    def __init__(self, *, persona: Persona, context: BrowserContext, store: Store, run_id: int,
                 session_id: int, cfg: RunConfig, guard: ScopeGuard,
                 artifacts: ArtifactManager | None, stop_event: asyncio.Event | None = None,
                 on_finding: Callable[[Finding, str], None] | None = None):
        self.persona = persona
        self.context = context
        self.store = store
        self.run_id = run_id
        self.session_id = session_id
        self.cfg = cfg
        self.guard = guard
        self.artifacts = artifacts
        self.stop_event = stop_event or asyncio.Event()
        self._on_finding_cb = on_finding
        self.collector = Collector(guard, cfg.slow_ms, self._record_finding)
        self.page: Page | None = None
        self.pages: list[Page] = []
        self.step_idx = 0
        self.findings = 0
        self.screenshots = 0
        self._new_finding = False
        self._cdp = None

    # ------------------------------------------------------------------ helpers for personas
    @property
    def page_url(self) -> str:
        return self.page.url if self.page and not self.page.is_closed() else self.guard.target

    @property
    def dialogs(self) -> list[str]:
        return self.collector.dialogs

    async def document_body(self) -> str:
        resp = self.collector.last_doc_response
        if resp is not None:
            with contextlib.suppress(Exception):
                return await resp.text()
        with contextlib.suppress(Exception):
            return await self.page.content()
        return ""

    async def dom_has_tag(self, tag: str) -> bool:
        with contextlib.suppress(Exception):
            return bool(await self.page.evaluate(
                "(t) => document.getElementsByTagName(t).length > 0", tag))
        return False

    # ------------------------------------------------------------------ findings
    def _record_finding(self, f: Finding) -> None:
        self.store.add_finding(self.run_id, f, self.session_id, self.step_idx, self.persona.name)
        self.findings += 1
        self._new_finding = True
        if self._on_finding_cb:
            self._on_finding_cb(f, self.persona.name)

    # ------------------------------------------------------------------ lifecycle
    async def _open_page(self) -> None:
        self.page = await self.context.new_page()
        self.pages.append(self.page)
        self.collector.attach(self.page)
        self.collector.throttled = False
        self._cdp = None

    async def run(self, deadline: float, max_steps: int | None) -> str:
        await self._open_page()
        status = "ok"
        try:
            await self._do(Action(kind="goto", target=self.guard.target, note="start"))
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                if max_steps is not None and self.step_idx + 1 >= max_steps:
                    break
                snap = await self._snapshot()
                if not self.guard.same_origin(snap.url):
                    action = Action(kind="goto", target=self.guard.target, note="left the app")
                else:
                    action = self.persona.next_action(snap)
                    action = await self.persona.prepare(action, snap)
                self.step_idx += 1
                await self._do(action)
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception as exc:  # keep the run alive; record what broke the agent
            status = "error"
            self.store.add_event(self.run_id, "log", {
                "level": "error", "message": f"{self.persona.name} agent crashed: {exc!r}",
                "session_id": self.session_id}, self.session_id)
        if status == "ok" and self.findings:
            status = "failed"
        return status

    async def _snapshot(self) -> PageSnapshot:
        for _ in range(2):
            try:
                if self.page is None or self.page.is_closed():
                    await self._open_page()
                return await take_snapshot(self.page)
            except Exception:
                await self._settle()
        return PageSnapshot(url=self.page_url)

    async def _do(self, action: Action) -> None:
        self.store.add_step(self.run_id, self.session_id, self.step_idx, action,
                            url=self.page_url, persona=self.persona.name)
        self.collector.begin_action(action)
        try:
            await self.execute(action)
        except Exception as exc:
            self.store.add_event(self.run_id, "log", {
                "level": "warn", "message": f"step {self.step_idx} failed: {str(exc)[:200]}",
                "session_id": self.session_id}, self.session_id)
            if self.page is None or self.page.is_closed():
                await self._open_page()
                with contextlib.suppress(Exception):
                    await self.page.goto(self.guard.target)
        await self.collector.flush()
        with contextlib.suppress(Exception):
            for f in await self.persona.inspect(self, action):
                self.collector.emit(f)
        await self.collector.wait_inflight_posts()
        self.collector.check_double_submit()
        self.collector.end_action()
        await self._maybe_screenshot()
        await asyncio.sleep(self.cfg.step_delay_ms / 1000)

    async def _maybe_screenshot(self) -> None:
        if not (self._new_finding and self.cfg.screenshots and self.artifacts):
            return
        self._new_finding = False
        if self.screenshots >= MAX_SCREENSHOTS or self.page is None or self.page.is_closed():
            return
        path = self.artifacts.screenshot_path(self.session_id, self.step_idx)
        with contextlib.suppress(Exception):
            await self.page.screenshot(path=str(path), timeout=5000)
            self.store.add_artifact(self.session_id, "screenshot", self.artifacts.rel(path),
                                    self.step_idx)
            self.screenshots += 1

    async def _settle(self) -> None:
        if self.page is None or self.page.is_closed():
            return
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=self.cfg.nav_timeout_ms)
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("networkidle", timeout=1500)

    async def close(self) -> list[Path]:
        """Close the context; return the raw video files produced (if any)."""
        videos = [p.video for p in self.pages if p.video]
        with contextlib.suppress(Exception):
            await self.context.close()
        paths = []
        for v in videos:
            with contextlib.suppress(Exception):
                paths.append(Path(await v.path()))
        return paths

    # ------------------------------------------------------------------ actions
    async def execute(self, a: Action) -> None:
        p = self.page
        k = a.kind
        if k == "goto":
            await p.goto(a.target, wait_until="domcontentloaded")
        elif k == "click":
            await p.click(a.target)
        elif k == "dblclick":
            await p.dblclick(a.target)
        elif k == "submit_form":
            await self._submit(a)
        elif k == "reload":
            await p.reload(wait_until="domcontentloaded")
        elif k == "back":
            await p.go_back(wait_until="domcontentloaded")
        elif k == "throttle":
            await self._throttle(a.target == "on")
        elif k == "close_tab":
            await self._close_and_reopen(a.target or self.guard.target)
        elif k == "wait":
            await asyncio.sleep(0.5)
        await self._settle()

    async def _fill(self, f: FieldValue) -> None:
        loc = self.page.locator(f.selector)
        if f.tag == "select":
            if f.value:
                try:
                    await loc.select_option(f.value)
                except Exception:
                    await loc.evaluate(SET_VALUE_JS, f.value)
            return
        if f.type in {"checkbox", "radio"}:
            if f.value:
                await loc.check()
            return
        try:
            await loc.fill(f.value, timeout=3000)
        except Exception:
            await loc.evaluate(SET_VALUE_JS, f.value)

    async def _submit(self, a: Action) -> None:
        p = self.page
        if a.bypass_validation:
            targets = [a.form_selector] if a.form_selector else [f.selector for f in a.fields]
            for sel in targets:
                with contextlib.suppress(Exception):
                    await p.locator(sel).evaluate(BYPASS_JS)
        for f in a.fields:
            with contextlib.suppress(Exception):
                await self._fill(f)
        last_field = next((f.selector for f in reversed(a.fields)
                           if f.type not in {"checkbox", "radio"} and f.tag != "select"), None)
        mode, submit = a.mode, a.submit_selector
        if mode == "click":
            await p.click(submit)
        elif mode == "dblclick":
            await p.dblclick(submit)
        elif mode == "enter":
            if last_field:
                await p.press(last_field, "Enter")
            elif submit:
                await p.click(submit)
        elif mode == "js":
            if a.form_selector:
                await p.locator(a.form_selector).evaluate("(f) => f.requestSubmit()")
            elif submit:
                await p.click(submit)
            elif last_field:
                await p.press(last_field, "Enter")
        elif mode == "click_reload":
            await p.click(submit, no_wait_after=True)
            await asyncio.sleep(0.05)
            await p.reload(wait_until="domcontentloaded")
        elif mode == "click_close":
            url = p.url
            await p.click(submit, no_wait_after=True)
            await asyncio.sleep(0.03)
            await self._close_and_reopen(url)

    async def _throttle(self, on: bool) -> None:
        if self._cdp is None:
            self._cdp = await self.context.new_cdp_session(self.page)
            await self._cdp.send("Network.enable")
        conditions = (
            {"offline": False, "latency": 400, "downloadThroughput": 50 * 1024,
             "uploadThroughput": 25 * 1024}
            if on else
            {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1}
        )
        await self._cdp.send("Network.emulateNetworkConditions", conditions)
        self.collector.throttled = on

    async def _close_and_reopen(self, url: str) -> None:
        with contextlib.suppress(Exception):
            await self.page.close(run_before_unload=False)
        await self._open_page()
        target = url if self.guard.same_origin(url) else self.guard.target
        await self.page.goto(target, wait_until="domcontentloaded")
