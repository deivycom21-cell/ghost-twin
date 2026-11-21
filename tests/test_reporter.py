"""Report generation is deterministic byte-for-byte (golden file).

Regenerate the golden file after an intentional change with:
    UPDATE_GOLDEN=1 pytest tests/test_reporter.py
"""

import os
from pathlib import Path

import pytest

from ghost_twin.models import Action, FieldValue, Finding, Severity
from ghost_twin.reporter import build_report, fingerprint, group_findings, write_report
from ghost_twin.reporter.dedupe import normalize_path

GOLDEN = Path(__file__).parent / "golden" / "BUG_REPORT.golden.md"
T = "http://localhost:3000"


def populate(store):
    run = store.create_run(T, {"users": 3, "seed": 1337}, seed=1337)
    # normal user: hits a 500 after filling a form
    s1 = store.create_session(run, 0, "normal_user")
    store.add_step(run, s1, 0, Action(kind="goto", target=T, note="start"), url="about:blank")
    store.add_step(run, s1, 1, Action(kind="click", target="#c", label="Contact"), url=T + "/")
    store.add_step(run, s1, 2, Action(kind="submit_form", label="Send", fields=[
        FieldValue("#n", "Ana García", name="name"), FieldValue("#m", "", name="message")]),
        url=T + "/contact")
    store.add_finding(run, Finding("http_5xx", Severity.HIGH, "Server error 500 on POST /contact",
                                   T + "/contact", "POST", 500, evidence="IndexError: boom"),
                      s1, 2, "normal_user")
    store.add_finding(run, Finding("console_error", Severity.MEDIUM,
                                   "console.error on /: Failed 1", T + "/", evidence="Failed 1"),
                      s1, 0, "normal_user")
    store.add_artifact(s1, "screenshot", "runs/1/screens/session-1-step-2.png", 2)
    store.add_artifact(s1, "video", "runs/1/videos/session-1.webm")
    store.finish_session(s1, "failed", "runs/1/videos/session-1.webm")
    # prober: SQL leak via URL param, seen twice with different payloads
    s2 = store.create_session(run, 1, "security_prober")
    store.add_step(run, s2, 0, Action(kind="goto", target=T, note="start"), url="about:blank")
    store.add_step(run, s2, 1, Action(kind="goto", target=T + "/search?q=%27", payload="'",
                                      payload_kind="sqli", param="q", note="probe sqli"), url=T)
    for payload in ("'", "\")"):
        store.add_finding(run, Finding(
            "sql_error_leak", Severity.CRITICAL, "Database error leaked on GET /search via `q`",
            T + "/search?q=x", "GET", 500, payload, "q",
            evidence="sqlite3.OperationalError: unrecognized token\n```oops```"), s2, 1,
            "security_prober")
    store.add_finding(run, Finding("console_error", Severity.MEDIUM,
                                   "console.error on /: Failed 2", T + "/", evidence="Failed 2"),
                      s2, 0, "security_prober")
    store.finish_session(s2, "failed")
    # api fuzzer
    s3 = store.create_session(run, 2, "api_fuzzer")
    store.add_step(run, s3, 0, Action(kind="api", target=f"GET {T}/api/items/abc",
                                      label="item_id: non-numeric"), url=f"{T}/api/items/abc")
    store.add_finding(run, Finding("http_5xx", Severity.HIGH, "API 500 on GET /api/items/{item_id}",
                                   f"{T}/api/items/abc", "GET", 500, "abc", "item_id",
                                   evidence="ValueError", detail={
                                       "path_template": "/api/items/{item_id}",
                                       "curl": f"curl -i -X GET {T}/api/items/abc"}),
                      s3, 0, "api_fuzzer")
    store.finish_session(s3, "failed")
    # health check
    store.add_finding(run, Finding("server_crash", Severity.CRITICAL, "Target app crashed", T,
                                   evidence="ConnectError"), None, None, "health_check")
    store.finish_run(run, "target_down")
    return run


def test_golden_report(clock_store, tmp_path):
    run = populate(clock_store)
    text = build_report(clock_store, run, data_dir=tmp_path, report_dir=tmp_path)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8", newline="\n")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_report_content(clock_store, tmp_path):
    run = populate(clock_store)
    text = build_report(clock_store, run, data_dir=tmp_path, report_dir=tmp_path)
    assert text.index("Target app crashed") < text.index("Database error leaked") < \
        text.index("Server error 500 on POST /contact")
    assert "## Instructions for the coding agent" in text
    assert "curl -i -X GET" in text
    assert "`runs/1/screens/session-1-step-2.png`" in text
    assert "````text" in text  # evidence containing ``` gets a longer fence
    assert "Other payloads that triggered it" in text
    assert "5 unique bugs from 7 raw findings" in text


def test_report_spanish(clock_store, tmp_path):
    run = populate(clock_store)
    text = build_report(clock_store, run, data_dir=tmp_path, lang="es")
    assert "Instrucciones para el agente de código" in text and "Pasos para reproducir" in text


def test_empty_report(clock_store, tmp_path):
    run = clock_store.create_run(T)
    clock_store.finish_run(run)
    assert "No bugs found" in build_report(clock_store, run, data_dir=tmp_path)


def test_write_report_also_copies_to_run_dir(clock_store, tmp_path):
    run = populate(clock_store)
    out = write_report(clock_store, run, tmp_path / "out" / "BUG_REPORT.md", data_dir=tmp_path)
    assert out.exists()
    assert (tmp_path / "runs" / str(run) / "BUG_REPORT.md").exists()
    # paths are relative to each report's own folder
    assert "../runs/1/screens/session-1-step-2.png" in out.read_text(encoding="utf-8")


def test_dedupe_groups(clock_store):
    run = populate(clock_store)
    bugs = group_findings(clock_store.findings(run))
    by_kind = {}
    for b in bugs:
        by_kind.setdefault(b.first["kind"], []).append(b)
    assert len(by_kind["sql_error_leak"]) == 1 and len(by_kind["sql_error_leak"][0].occurrences) == 2
    assert len(by_kind["console_error"]) == 1  # "Failed 1"/"Failed 2" normalise to the same message
    assert by_kind["console_error"][0].personas == ["normal_user", "security_prober"]
    assert [b.severity for b in bugs] == sorted([b.severity for b in bugs], key=lambda s: -s.rank)


@pytest.mark.parametrize("url,expected", [
    ("http://h/users/42/edit", "/users/{id}/edit"),
    ("http://h/o/550e8400-e29b-41d4-a716-446655440000", "/o/{id}"),
    ("http://h/", "/"), ("http://h/search?q=1", "/search"),
])
def test_normalize_path(url, expected):
    assert normalize_path(url) == expected


def test_fingerprint_api_groups_by_template():
    a = {"kind": "http_5xx", "url": "http://h/api/items/abc", "method": "GET", "param": "item_id",
         "detail": {"path_template": "/api/items/{item_id}"}}
    b = {**a, "url": "http://h/api/items/%27", "param": "other"}
    assert fingerprint(a) == fingerprint(b)
