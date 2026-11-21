"""Group raw findings into unique bugs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ghost_twin.models import Severity

_ID_SEG = re.compile(r"/(\d+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{24,})(?=/|$)", re.I)
_DIGITS = re.compile(r"\d+")
MESSAGE_KINDS = {"console_error", "js_exception"}


def normalize_path(url: str) -> str:
    return _ID_SEG.sub("/{id}", urlsplit(url).path or "/")


def fingerprint(f: dict[str, Any]) -> str:
    kind = f["kind"]
    if kind == "server_crash":
        return kind
    if kind in MESSAGE_KINDS:
        first = (f.get("evidence") or f.get("title") or "").strip().splitlines()[0:1]
        msg = _DIGITS.sub("N", first[0] if first else "")[:160]
        return f"{kind}|{msg}"
    path = (f.get("detail") or {}).get("path_template") or normalize_path(f["url"])
    method = (f.get("method") or "GET").upper()
    if kind in {"slow_response", "double_submit", "http_4xx"}:
        return f"{kind}|{method}|{path}"
    if kind == "http_5xx" and (f.get("detail") or {}).get("path_template"):
        return f"{kind}|{method}|{path}"  # API: one bug per endpoint, cases listed as payloads
    if kind == "request_failed":
        return f"{kind}|{method}|{path}|{f.get('evidence', '')[:60]}"
    return f"{kind}|{method}|{path}|{f.get('param') or ''}"


@dataclass
class Bug:
    fingerprint: str
    first: dict[str, Any]
    occurrences: list[dict[str, Any]] = field(default_factory=list)

    @property
    def severity(self) -> Severity:
        return max((Severity.parse(o["severity"]) for o in self.occurrences), key=lambda s: s.rank)

    @property
    def personas(self) -> list[str]:
        return sorted({o.get("persona") or "health_check" for o in self.occurrences})

    @property
    def urls(self) -> list[str]:
        seen: list[str] = []
        for o in self.occurrences:
            if o["url"] not in seen:
                seen.append(o["url"])
        return seen

    @property
    def payloads(self) -> list[str]:
        seen: list[str] = []
        for o in self.occurrences:
            p = o.get("payload")
            if p is not None and p not in seen:
                seen.append(p)
        return seen


def group_findings(findings: list[dict[str, Any]]) -> list[Bug]:
    bugs: dict[str, Bug] = {}
    for f in sorted(findings, key=lambda x: x["id"]):
        fp = fingerprint(f)
        bug = bugs.get(fp)
        if bug is None:
            bug = bugs[fp] = Bug(fp, f)
        bug.occurrences.append(f)
    return sorted(bugs.values(), key=lambda b: (
        -b.severity.rank, b.first["kind"] != "server_crash", b.first["id"]))
