"""API mode: discover an OpenAPI spec on the target and fuzz its endpoints with httpx."""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx

from ghost_twin.models import Action, Finding
from ghost_twin.personas.payloads import SQLI_AGGRESSIVE, SQLI_SAFE, TRAVERSAL, looks_pathy
from ghost_twin.recorder import detectors as d
from ghost_twin.safety import ScopeGuard, is_destructive_url

SPEC_PATHS = ["/openapi.json", "/swagger.json", "/api/openapi.json", "/v3/api-docs",
              "/api-docs", "/docs/openapi.json", "/api/swagger.json", "/swagger/v1/swagger.json"]
METHODS = ["get", "post", "put", "patch", "delete"]
HUGE = "A" * 10_000


@dataclass
class ApiCase:
    method: str
    template: str
    path: str
    query: dict[str, Any] = field(default_factory=dict)
    body: Any = None
    raw_body: str | None = None
    label: str = "baseline"
    payload: str | None = None
    payload_kind: str | None = None
    param: str | None = None

    def url(self, base: str) -> str:
        u = urljoin(base.rstrip("/") + "/", self.path.lstrip("/"))
        return f"{u}?{urlencode(self.query, doseq=True)}" if self.query else u

    def body_text(self) -> str | None:
        if self.raw_body is not None:
            return self.raw_body
        if self.body is None:
            return None
        return json.dumps(self.body, ensure_ascii=False)

    def curl(self, base: str) -> str:
        parts = ["curl", "-i", "-X", self.method.upper(), shlex.quote(self.url(base))]
        body = self.body_text()
        if body is not None:
            shown = body if len(body) <= 500 else body[:200] + "…"
            parts += ["-H", shlex.quote("Content-Type: application/json"), "-d", shlex.quote(shown)]
        return " ".join(parts)


# --------------------------------------------------------------------------- schema helpers


def resolve(spec: dict, schema: Any, depth: int = 0) -> dict:
    if not isinstance(schema, dict) or depth > 8:
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = spec
        for part in ref[2:].split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return resolve(spec, node, depth + 1)
    for combo in ("allOf", "oneOf", "anyOf"):
        if combo in schema and schema[combo]:
            if combo == "allOf":
                merged: dict = {"type": "object", "properties": {}, "required": []}
                for sub in schema[combo]:
                    s = resolve(spec, sub, depth + 1)
                    merged["properties"].update(s.get("properties", {}))
                    merged["required"] += s.get("required", [])
                return merged
            return resolve(spec, schema[combo][0], depth + 1)
    return schema


def sample(spec: dict, schema: Any, name: str = "", depth: int = 0) -> Any:
    s = resolve(spec, schema)
    if "example" in s:
        return s["example"]
    if "default" in s:
        return s["default"]
    if s.get("enum"):
        return s["enum"][0]
    t = s.get("type")
    if t is None and "properties" in s:
        t = "object"
    if t == "integer":
        return int(s.get("minimum", 1) or 1)
    if t == "number":
        return float(s.get("minimum", 1.5) or 1.5)
    if t == "boolean":
        return True
    if t == "array":
        return [sample(spec, s.get("items", {}), name, depth + 1)] if depth < 4 else []
    if t == "object":
        if depth > 4:
            return {}
        return {k: sample(spec, v, k, depth + 1) for k, v in s.get("properties", {}).items()}
    fmt = s.get("format", "")
    if fmt == "email" or "email" in name.lower():
        return "ghost@example.com"
    if fmt == "date":
        return "2026-01-15"
    if fmt == "date-time":
        return "2026-01-15T10:00:00Z"
    if fmt == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    if fmt in {"uri", "url"}:
        return "https://example.com"
    return "ghost"


def fuzz_values(spec: dict, schema: Any, name: str, aggressive: bool) -> list[tuple[str, Any, str]]:
    """(label, value, payload_kind) tuples for a single scalar parameter."""
    s = resolve(spec, schema)
    t = s.get("type", "string")
    out: list[tuple[str, Any, str]] = []
    if t in {"integer", "number"}:
        out += [("non-numeric", "abc", "type"), ("negative", -1, "edge"), ("zero", 0, "edge"),
                ("overflow", 2**63, "edge")]
    out += [(f"sqli {p!r}", p, "sqli") for p in SQLI_SAFE[:2]]
    if aggressive:
        out += [(f"sqli {p!r}", p, "sqli") for p in SQLI_AGGRESSIVE]
    if looks_pathy(name):
        out += [("traversal", p, "traversal") for p in TRAVERSAL[:2]]
    out += [("huge string", HUGE, "huge"), ("empty", "", "edge")]
    return out


def build_cases(spec: dict, allow_destructive: bool = False,
                aggressive: bool = False) -> list[ApiCase]:
    cases: list[ApiCase] = []
    for template, ops in (spec.get("paths") or {}).items():
        if not isinstance(ops, dict) or (is_destructive_url(template) and not allow_destructive):
            continue
        common = ops.get("parameters", [])
        for method in METHODS:
            op = ops.get(method)
            if not isinstance(op, dict):
                continue
            if method == "delete" and not allow_destructive:
                continue
            params = [resolve(spec, p) for p in common + op.get("parameters", [])]
            path_params = {p["name"]: p for p in params if p.get("in") == "path"}
            query_params = {p["name"]: p for p in params if p.get("in") == "query"}

            def build_path(overrides: dict[str, Any], _pp=path_params, _t=template) -> str:
                path = _t
                for n, p in _pp.items():
                    v = overrides.get(n, sample(spec, p.get("schema", {}), n))
                    path = path.replace("{" + n + "}", str(v))
                return path

            base_query = {n: sample(spec, p.get("schema", {}), n)
                          for n, p in query_params.items() if p.get("required")}
            body_schema = None
            rb = resolve(spec, op.get("requestBody", {}))
            content = rb.get("content", {}) if rb else {}
            if "application/json" in content:
                body_schema = resolve(spec, content["application/json"].get("schema", {}))
            base_body = sample(spec, body_schema) if body_schema is not None else None

            cases.append(ApiCase(method, template, build_path({}), dict(base_query), base_body))
            for n, p in {**path_params, **query_params}.items():
                for label, value, kind in fuzz_values(spec, p.get("schema", {}), n, aggressive):
                    if n in path_params:
                        path, query = build_path({n: value}), dict(base_query)
                    else:
                        path, query = build_path({}), {**base_query, n: value}
                    cases.append(ApiCase(method, template, path, query, base_body,
                                         label=f"{n}: {label}", payload=str(value),
                                         payload_kind=kind, param=n))
            if isinstance(base_body, dict) and body_schema is not None:
                props = body_schema.get("properties", {})
                path = build_path({})
                cases.append(ApiCase(method, template, path, dict(base_query), {}, label="empty body",
                                     payload="{}", payload_kind="edge"))
                cases.append(ApiCase(method, template, path, dict(base_query), raw_body="{\"broken\":",
                                     label="malformed JSON", payload="{\"broken\":",
                                     payload_kind="edge"))
                for req_name in body_schema.get("required", []):
                    b = {k: v for k, v in base_body.items() if k != req_name}
                    cases.append(ApiCase(method, template, path, dict(base_query), b,
                                         label=f"missing required `{req_name}`", param=req_name,
                                         payload="<missing>", payload_kind="edge"))
                for prop, pschema in props.items():
                    t = resolve(spec, pschema).get("type", "string")
                    wrong = "not-a-number" if t in {"integer", "number"} else (
                        12345 if t == "string" else "wrong-type")
                    for label, value, kind in [("wrong type", wrong, "type"), ("null", None, "edge")]:
                        cases.append(ApiCase(method, template, path, dict(base_query),
                                             {**base_body, prop: value}, label=f"{prop}: {label}",
                                             payload=json.dumps(value), payload_kind=kind,
                                             param=prop))
                    if t == "string":
                        for label, value, kind in fuzz_values(spec, pschema, prop, aggressive):
                            if kind in {"sqli", "traversal", "huge"}:
                                cases.append(ApiCase(method, template, path, dict(base_query),
                                                     {**base_body, prop: value},
                                                     label=f"{prop}: {label}", payload=str(value),
                                                     payload_kind=kind, param=prop))
    return cases


# --------------------------------------------------------------------------- runtime


async def discover_spec(client: httpx.AsyncClient, base: str,
                        explicit: str | None = None) -> tuple[dict | None, str | None]:
    candidates = [explicit] if explicit else [urljoin(base.rstrip("/") + "/", p.lstrip("/"))
                                              for p in SPEC_PATHS]
    for url in candidates:
        try:
            r = await client.get(url)
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            try:
                spec = r.json()
            except ValueError:
                continue
            if isinstance(spec, dict) and ("openapi" in spec or "swagger" in spec) and "paths" in spec:
                return spec, url
    return None, None


def analyze_response(case: ApiCase, url: str, status: int, body: str, elapsed_ms: float,
                     slow_ms: int) -> list[Finding]:
    method = case.method.upper()
    out: list[Finding] = []
    sql = d.detect_sql_leak(body, url, method, status, case.payload, case.param) \
        if (status >= 500 or case.payload_kind == "sqli") else None
    if sql:
        out.append(sql)
    elif status >= 500:
        f = d.detect_http_status(url, method, status, "api", body, case.payload, case.param)
        if f:
            f.title = f"API {status} on {method} {case.template}" + (
                f" ({case.label})" if case.label != "baseline" else "")
            out.append(f)
    if case.payload_kind == "traversal":
        t = d.detect_traversal(body, url, case.payload, method, case.param, status)
        if t:
            out.append(t)
    slow = d.detect_slow(url, method, elapsed_ms, slow_ms, "api")
    if slow:
        out.append(slow)
    return out


async def run_api_fuzz(*, base: str, guard: ScopeGuard, spec: dict, record_step: Callable,
                       record_finding: Callable, deadline: float, stop_event: asyncio.Event,
                       max_requests: int = 150, slow_ms: int = 2000, allow_destructive: bool = False,
                       aggressive: bool = False, delay_s: float = 0.02) -> int:
    cases = build_cases(spec, allow_destructive, aggressive)
    seen: set[tuple] = set()
    sent = 0
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        for idx, case in enumerate(cases[:max_requests]):
            if stop_event.is_set() or time.monotonic() > deadline:
                break
            url = case.url(base)
            if not guard.same_origin(url):
                continue
            body = case.body_text()
            action = Action(kind="api", target=f"{case.method.upper()} {url}", label=case.label,
                            payload=case.payload, payload_kind=case.payload_kind, param=case.param,
                            note=body[:300] if body else "")
            record_step(idx, action)
            headers = {"Content-Type": "application/json"} if body is not None else {}
            t0 = time.perf_counter()
            try:
                r = await client.request(case.method.upper(), url, content=body, headers=headers)
                text, status = r.text[:300_000], r.status_code
            except httpx.HTTPError:
                continue
            finally:
                sent += 1
            elapsed = (time.perf_counter() - t0) * 1000
            for f in analyze_response(case, url, status, text, elapsed, slow_ms):
                key = (f.kind, case.method, case.template, case.param)
                if key in seen:
                    continue
                seen.add(key)
                f.detail.update({"path_template": case.template, "curl": case.curl(base),
                                 "case": case.label})
                record_finding(idx, f)
            await asyncio.sleep(delay_s)
    return sent

