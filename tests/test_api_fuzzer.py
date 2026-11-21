import asyncio
import time

import httpx

from ghost_twin.api.openapi_fuzzer import (
    ApiCase,
    build_cases,
    discover_spec,
    resolve,
    run_api_fuzz,
    sample,
)
from ghost_twin.demo.buggy_app import OPENAPI
from ghost_twin.safety import ScopeGuard


def test_resolve_and_sample():
    schema = resolve(OPENAPI, {"$ref": "#/components/schemas/NewItem"})
    assert schema["required"] == ["name", "price"]
    assert sample(OPENAPI, schema) == {"name": "ghost", "price": 1.5}


def test_build_cases_covers_params_and_bodies():
    cases = build_cases(OPENAPI)
    labels = {(c.method, c.template, c.label) for c in cases}
    assert ("get", "/api/items/{item_id}", "item_id: non-numeric") in labels
    assert ("post", "/api/items", "missing required `price`") in labels
    assert ("post", "/api/items", "malformed JSON") in labels
    assert any(c.path == "/api/items/abc" for c in cases)
    assert build_cases(OPENAPI) == cases  # deterministic


def test_delete_skipped_unless_allowed():
    spec = {"openapi": "3.0.0", "paths": {"/api/x/{id}": {"delete": {"parameters": [
        {"name": "id", "in": "path", "schema": {"type": "integer"}}]}}}}
    assert build_cases(spec) == []
    assert build_cases(spec, allow_destructive=True)


def test_curl_rendering():
    c = ApiCase("post", "/api/items", "/api/items", body={"name": "it's"})
    cmd = c.curl("http://localhost:3000")
    assert cmd.startswith("curl -i -X POST http://localhost:3000/api/items")
    assert "Content-Type: application/json" in cmd


async def test_fuzz_against_buggy_app(buggy_app):
    async with httpx.AsyncClient(trust_env=False) as client:
        spec, url = await discover_spec(client, buggy_app.url)
    assert spec and url.endswith("/openapi.json")
    steps, findings = [], []
    sent = await run_api_fuzz(
        base=buggy_app.url, guard=ScopeGuard(buggy_app.url), spec=spec,
        record_step=lambda i, a: steps.append(a), record_finding=lambda i, f: findings.append(f),
        deadline=time.monotonic() + 30, stop_event=asyncio.Event(), delay_s=0)
    assert sent == len(steps) > 10
    templates = {(f.method, f.detail["path_template"]) for f in findings if f.kind == "http_5xx"}
    assert ("GET", "/api/items/{item_id}") in templates
    assert ("POST", "/api/items") in templates
    assert all("curl" in f.detail for f in findings)


async def test_no_spec_found(buggy_app):
    async with httpx.AsyncClient(trust_env=False) as client:
        spec, _ = await discover_spec(client, buggy_app.url, buggy_app.url + "/nope.json")
    assert spec is None
