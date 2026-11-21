"""security_prober: safe SQLi / reflected XSS / path traversal probes on params and inputs."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ghost_twin.models import Action, FieldValue, Finding, PageSnapshot
from ghost_twin.personas.base import Persona, form_key, norm_url
from ghost_twin.personas.datagen import valid_value
from ghost_twin.personas.payloads import Payload, payloads_for
from ghost_twin.recorder.detectors import detect_reflected_xss

MAX_PROBES_PER_PATH = 24


@dataclass
class Probe:
    kind: str  # url | form
    payload: Payload
    param: str
    url: str = ""          # url probe: final URL; form probe: page holding the form
    form_key: str = ""
    attempts: int = 0


def with_param(url: str, name: str, value: str) -> str:
    p = urlsplit(url)
    pairs = [(k, value if k == name else v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(pairs), ""))


class SecurityProber(Persona):
    name = "security_prober"
    emoji = "🕵️"
    description = "Probes params and inputs with safe SQLi, reflected-XSS and traversal payloads."

    def __init__(self, ctx):
        super().__init__(ctx)
        self.queue: deque[Probe] = deque()
        self.tried: set[tuple] = set()
        self.per_path: dict[str, int] = {}

    # ------------------------------------------------------------------ discovery
    def _enqueue(self, path: str, param: str, payload: Payload, probe: Probe) -> None:
        key = (path, param, payload.kind, "xss" if payload.kind == "xss" else payload.value)
        if key in self.tried or self.per_path.get(path, 0) >= MAX_PROBES_PER_PATH:
            return
        self.tried.add(key)
        self.per_path[path] = self.per_path.get(path, 0) + 1
        self.queue.append(probe)

    def _discover(self, snap: PageSnapshot) -> None:
        urls = [snap.url] + [lk.href for lk in self.safe_links(snap)]
        for url in urls:
            if not self.ctx.guard.same_origin(url):
                continue
            p = urlsplit(url)
            for name, value in parse_qsl(p.query, keep_blank_values=True):
                for pl in payloads_for(name, value, self.rng, self.ctx.aggressive):
                    self._enqueue(p.path, name, pl,
                                  Probe("url", pl, name, url=with_param(url, name, pl.value)))
        for form in self.safe_forms(snap):
            path = urlsplit(form.action).path
            for inp in form.fillable_inputs:
                if not inp.texty or not inp.name:
                    continue
                for pl in payloads_for(inp.name, "", self.rng, self.ctx.aggressive):
                    self._enqueue(path, inp.name, pl,
                                  Probe("form", pl, inp.name, url=snap.url, form_key=form_key(form)))

    # ------------------------------------------------------------------ decide
    def decide(self, snap: PageSnapshot) -> Action:
        self._discover(snap)
        while self.queue:
            probe = self.queue[0]
            if probe.kind == "url":
                self.queue.popleft()
                return Action(kind="goto", target=probe.url, payload=probe.payload.value,
                              payload_kind=probe.payload.kind, param=probe.param,
                              marker=probe.payload.marker, note=f"probe {probe.payload.kind}")
            form = next((f for f in self.safe_forms(snap) if form_key(f) == probe.form_key), None)
            if form is not None and norm_url(snap.url) == norm_url(probe.url):
                self.queue.popleft()
                fields = []
                for i in form.fillable_inputs:
                    value = probe.payload.value if i.name == probe.param else valid_value(
                        i, self.rng, self.identity)
                    fields.append(FieldValue(i.selector, value, i.type, i.tag, i.name))
                return self.submit_action(
                    form, fields, mode="click", bypass_validation=True,
                    payload=probe.payload.value, payload_kind=probe.payload.kind,
                    param=probe.param, marker=probe.payload.marker,
                    note=f"probe {probe.payload.kind}")
            probe.attempts += 1
            if probe.attempts > 2:
                self.queue.popleft()
                continue
            return Action(kind="goto", target=probe.url, note="return to form under test")
        return self.explore(snap)

    # ------------------------------------------------------------------ inspect
    async def inspect(self, runner, action: Action) -> list[Finding]:
        if action.payload_kind != "xss" or not action.marker:
            return []
        body = await runner.document_body()
        dom_hit = await runner.dom_has_tag(action.marker)
        dialog_hit = any(action.marker in d for d in runner.dialogs)
        f = detect_reflected_xss(runner.page_url, action.marker, action.payload or "", body=body,
                                 dom_hit=dom_hit, dialog_hit=dialog_hit,
                                 method="POST" if action.kind == "submit_form" and
                                 action.target and action.mode != "js" else "GET",
                                 param=action.param)
        return [f] if f else []
