import pytest

from ghost_twin.models import Severity
from ghost_twin.recorder import detectors as d


@pytest.mark.parametrize("body", [
    "You have an error in your SQL syntax; check the manual that corresponds to your MySQL",
    "Unclosed quotation mark after the character string ''.",
    "ERROR: syntax error at or near \"'\" LINE 1",
    "sqlite3.OperationalError: unrecognized token: \"'\"",
    "ORA-01756: quoted string not properly terminated",
    "SQLSTATE[42000]: Syntax error",
    "PrismaClientKnownRequestError: Invalid query",
    "SequelizeDatabaseError: near \"'\": syntax error",
    "psycopg2.errors.SyntaxError: unterminated quoted string",
])
def test_sql_signatures(body):
    f = d.detect_sql_leak(body, "http://l/s?q='", payload="'", param="q", status=500)
    assert f and f.kind == "sql_error_leak" and f.severity is Severity.CRITICAL and f.param == "q"


@pytest.mark.parametrize("body", ["All good", "We sell SQL books", "<h1>Not found</h1>", ""])
def test_no_sql_false_positive(body):
    assert d.detect_sql_leak(body, "http://l/") is None


def test_traversal_needs_payload_and_signature():
    body = "root:x:0:0:root:/root:/bin/bash"
    assert d.detect_traversal(body, "http://l/f", "../etc/passwd", param="f").kind == "path_traversal"
    assert d.detect_traversal(body, "http://l/f", None) is None
    assert d.detect_traversal("[fonts]\n[extensions]", "http://l/f", "..\\win.ini") is not None
    assert d.detect_traversal("hello", "http://l/f", "../etc/passwd") is None


def test_marker_reflection_escaped_vs_raw():
    assert d.is_marker_reflected("<p>Results: <gtxab12cd></p>", "gtxab12cd")
    assert d.is_marker_reflected("<GTXAB12CD x=1>", "gtxab12cd")
    assert not d.is_marker_reflected("<p>&lt;gtxab12cd&gt;</p>", "gtxab12cd")
    assert not d.is_marker_reflected("<p>gtxab12cd</p>", "gtxab12cd")


def test_reflected_xss_from_dom_or_dialog():
    assert d.detect_reflected_xss("http://l/", "gtx1", "<gtx1>", dom_hit=True).kind == "reflected_xss"
    f = d.detect_reflected_xss("http://l/", "gtx1", "<svg>", dialog_hit=True)
    assert "alert fired" in f.evidence
    assert d.detect_reflected_xss("http://l/", "gtx1", "<gtx1>", body="&lt;gtx1&gt;") is None


@pytest.mark.parametrize("status,rtype,kind", [
    (500, "document", "http_5xx"), (503, "fetch", "http_5xx"), (404, "script", "http_4xx"),
    (404, "fetch", "http_4xx"), (404, "document", None), (422, "fetch", None), (200, "document", None),
])
def test_http_status(status, rtype, kind):
    f = d.detect_http_status("http://l/x", "GET", status, rtype)
    assert (f.kind if f else None) == kind


def test_http_status_ignores_favicon_and_payload_4xx():
    assert d.detect_http_status("http://l/favicon.ico", "GET", 404, "image") is None
    assert d.detect_http_status("http://l/api", "GET", 404, "fetch", payload="'") is None


def test_5xx_title_mentions_payload():
    f = d.detect_http_status("http://l/s", "POST", 500, "document", "trace", "'", "q")
    assert "`q`" in f.title and f.payload == "'" and f.evidence == "trace"


def test_slow():
    assert d.detect_slow("http://l/s", "GET", 2500, 2000).kind == "slow_response"
    assert d.detect_slow("http://l/s", "GET", 1999, 2000) is None


def test_console_filters_noise():
    assert d.detect_console("error", "Boom", "http://l/").kind == "console_error"
    assert d.detect_console("warning", "Boom", "http://l/") is None
    assert d.detect_console("error", "Failed to load resource: the server responded with 500",
                            "http://l/") is None


def test_page_error_and_request_failed():
    assert d.detect_page_error("x is not defined", "http://l/").severity is Severity.HIGH
    assert d.detect_request_failed("http://l/a", "GET", "net::ERR_ABORTED") is None
    assert d.detect_request_failed("http://l/a", "GET", "net::ERR_CONNECTION_RESET").kind == \
        "request_failed"


def _post(ts, status, body="h1"):
    return {"ts": ts, "url": "http://l/api/signup", "method": "POST", "body_hash": body,
            "status": status}


def test_double_submit():
    assert d.detect_double_submit([_post(0, 201), _post(0.2, 201)]).kind == "double_submit"
    assert d.detect_double_submit([_post(0, 201), _post(0.2, None)]) is None  # second aborted
    assert d.detect_double_submit([_post(0, 201), _post(3, 201)]) is None  # too far apart
    assert d.detect_double_submit([_post(0, 201), _post(0.2, 201, "other")]) is None
    assert d.detect_double_submit([]) is None
