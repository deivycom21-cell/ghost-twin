"""Persona base class. ``decide()`` is pure (snapshot in, action out) so it is unit-testable;
anything that needs the live page goes through the ``AgentRunner`` passed to ``inspect()``."""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from ghost_twin.models import Action, FieldValue, Finding, FormInfo, LinkInfo, PageSnapshot
from ghost_twin.personas.datagen import Identity, valid_value
from ghost_twin.safety import ScopeGuard, is_destructive_label, is_destructive_url

if TYPE_CHECKING:  # pragma: no cover
    from ghost_twin.engine.agent import AgentRunner

DOWNLOAD_EXT = (".pdf", ".zip", ".gz", ".tar", ".exe", ".dmg", ".msi", ".apk", ".iso", ".mp4",
                ".mp3", ".csv", ".xlsx", ".docx")


@dataclass
class AgentContext:
    start_url: str
    guard: ScopeGuard
    seed: int
    allow_destructive: bool = False
    aggressive: bool = False
    ai: Any = None  # ghost_twin.ai.AIProvider | None


def norm_url(url: str) -> str:
    p = urlsplit(url)
    return f"{p.path or '/'}?{p.query}" if p.query else (p.path or "/")


def form_key(form: FormInfo) -> str:
    names = ",".join(sorted(i.name or i.type for i in form.fillable_inputs))
    return f"{form.method}:{urlsplit(form.action).path}:{names}"


class Persona(ABC):
    name: ClassVar[str] = "base"
    emoji: ClassVar[str] = "👻"
    description: ClassVar[str] = ""

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx
        self.rng = random.Random(ctx.seed)
        self.identity = Identity.generate(self.rng)
        self.visited: set[str] = set()
        self.step = 0

    # ------------------------------------------------------------------ API
    @abstractmethod
    def decide(self, snap: PageSnapshot) -> Action:
        """Choose the next action for this snapshot (pure, deterministic given the seed)."""

    def next_action(self, snap: PageSnapshot) -> Action:
        self.visited.add(norm_url(snap.url))
        self.step += 1
        return self.decide(snap)

    async def prepare(self, action: Action, snap: PageSnapshot) -> Action:  # noqa: ARG002
        """Async hook to enrich an action (e.g. AI-generated form data)."""
        return action

    async def inspect(self, runner: AgentRunner, action: Action) -> list[Finding]:  # noqa: ARG002
        """Async hook run after each action, with access to the live page."""
        return []

    # ------------------------------------------------------------------ helpers
    def link_ok(self, link: LinkInfo) -> bool:
        if not self.ctx.guard.same_origin(link.href):
            return False
        if urlsplit(link.href).path.lower().endswith(DOWNLOAD_EXT):
            return False
        if self.ctx.allow_destructive:
            return True
        return not (is_destructive_label(link.text) or is_destructive_url(link.href))

    def safe_links(self, snap: PageSnapshot) -> list[LinkInfo]:
        return [link for link in snap.links if self.link_ok(link)]

    def safe_buttons(self, snap: PageSnapshot):
        if self.ctx.allow_destructive:
            return list(snap.buttons)
        return [b for b in snap.buttons if not is_destructive_label(b.label)]

    def form_ok(self, form: FormInfo) -> bool:
        if not form.fillable_inputs:
            return False
        if form.method != "js" and not self.ctx.guard.same_origin(form.action):
            return False
        if self.ctx.allow_destructive:
            return True
        return not (is_destructive_label(form.submit_label) or is_destructive_url(form.action))

    def safe_forms(self, snap: PageSnapshot) -> list[FormInfo]:
        return [f for f in snap.forms if self.form_ok(f)]

    def valid_fields(self, form: FormInfo) -> list[FieldValue]:
        return [
            FieldValue(i.selector, valid_value(i, self.rng, self.identity), i.type, i.tag, i.name)
            for i in form.fillable_inputs
        ]

    def submit_action(self, form: FormInfo, fields: list[FieldValue], mode: str = "click",
                      **kw) -> Action:
        if not form.submit_selector and mode in {"click", "dblclick", "click_reload", "click_close"}:
            mode = "enter" if form.selector else "js"
        return Action(kind="submit_form", form_selector=form.selector,
                      submit_selector=form.submit_selector, fields=fields, mode=mode,
                      label=form.submit_label, target=form.action, **kw)

    def explore(self, snap: PageSnapshot) -> Action:
        links = self.safe_links(snap)
        fresh = [lk for lk in links if norm_url(lk.href) not in self.visited]
        if fresh:
            link = fresh[0] if self.rng.random() < 0.5 else self.rng.choice(fresh)
            return Action(kind="click", target=link.selector, label=link.text or link.href,
                          note=link.href)
        if links and self.rng.random() < 0.7:
            link = self.rng.choice(links)
            return Action(kind="click", target=link.selector, label=link.text or link.href,
                          note=link.href)
        return Action(kind="goto", target=self.ctx.start_url, note="back to start")
