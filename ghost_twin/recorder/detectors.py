"""Pure detection functions: plain data in, ``Finding | None`` out. No browser, no I/O."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from ghost_twin.models import Finding, Severity

SQL_ERROR_PATTERNS = [
    r"you have an error in your sql syntax",
    r"warning:\s*mysqli?_",
    r"mysql_fetch_",
    r"com\.mysql\.jdbc",
    r"unclosed quotation mark after the character string",
    r"microsoft ole db provider for (sql server|odbc)",
    r"incorrect syntax near",
    r"quoted string not properly terminated",
    r"ora-\d{5}",
    r"pg::syntaxerror",
    r"syntax error at or near",
    r"unterminated quoted (string|identifier)",
    r"org\.postgresql\.util\.psqlexception",
    r"psycopg2?\.(errors|programmingerror)",
    r"sqlite3?\.operationalerror",
    r"sqlite_error",
    r"sqlite_exception",
    r"unrecognized token: ",
    r"near \"[^\"]*\": syntax error",
    r"sqlstate\[",
    r"sequelizedatabaseerror",
    r"er_parse_error",
    r"pdoexception",
    r"knex.*syntax",
    r"prismaclient(unknown|known)requesterror",
]
_SQL_RE = re.compile("|".join(f"(?:{p})" for p in SQL_ERROR_PATTERNS), re.IGNORECASE)

TRAVERSAL_SIGNATURES = [
    re.compile(r"root:[x*]?:0:0:"),
    re.compile(r"daemon:[x*]?:1:1:"),
    re.compile(r"\[fonts\]", re.I),
    re.compile(r"for 16-bit app support", re.I),
    re.compile(r"\[boot loader\]", re.I),
]

STATIC_RESOURCE_TYPES = {"script", "stylesheet", "image", "font", "media"}
API_RESOURCE_TYPES = {"fetch", "xhr"}
IGNORED_PATHS = {"/favicon.ico", "/robots.txt", "/apple-touch-icon.png",
                 "/apple-touch-icon-precomposed.png"}
IGNORED_FAILURES = ("net::ERR_ABORTED", "NS_BINDING_ABORTED", "net::ERR_BLOCKED_BY_CLIENT")
CONSOLE_NOISE = ("Failed to load resource", "[vite]", "Download the React DevTools",
                 "[HMR]", "webpack-dev-server")


def _path(url: str) -> str:
    p = urlsplit(url)
    return p.path or "/"


def _snippet(text: str, around: re.Match | None = None, size: int = 600) -> str:
    if not text:
        return ""
    if around is None:
        return text[:size]
    start = max(0, around.start() - size // 3)
    return text[start:start + size]


def find_sql_error(body: str) -> re.Match | None:
    return _SQL_RE.search(body or "")


def detect_http_status(url: str, method: str, status: int, resource_type: str,
                       body: str = "", payload: str | None = None,
                       param: str | None = None) -> Finding | None:
    if status >= 500:
        title = f"Server error {status} on {method} {_path(url)}"
        if payload is not None:
            title += f" when `{param or 'input'}` = {payload!r}"
        return Finding(
            kind="http_5xx", severity=Severity.HIGH, title=title, url=url, method=method,
            status=status, payload=payload, param=param, evidence=_snippet(body, size=1500),
            detail={"resource_type": resource_type},
        )
    if 400 <= status < 500 and _path(url) not in IGNORED_PATHS:
        broken_asset = resource_type in STATIC_RESOURCE_TYPES
        broken_api = resource_type in API_RESOURCE_TYPES and status in {404, 405} and payload is None
        if broken_asset or broken_api:
            what = "Broken asset" if broken_asset else "API call failed"
            return Finding(
                kind="http_4xx", severity=Severity.LOW,
                title=f"{what}: {status} on {method} {_path(url)}", url=url, method=method,
                status=status, evidence=_snippet(body, size=400),
                detail={"resource_type": resource_type},
            )
    return None


def detect_slow(url: str, method: str, elapsed_ms: float, threshold_ms: int,
                resource_type: str = "document") -> Finding | None:
    if elapsed_ms is None or elapsed_ms < threshold_ms:
        return None
    return Finding(
        kind="slow_response", severity=Severity.LOW,
        title=f"Slow response ({elapsed_ms / 1000:.1f}s) on {method} {_path(url)}",
        url=url, method=method, evidence=f"{elapsed_ms:.0f} ms (threshold {threshold_ms} ms)",
        detail={"elapsed_ms": round(elapsed_ms), "resource_type": resource_type},
    )


def detect_console(msg_type: str, text: str, url: str) -> Finding | None:
    if msg_type != "error" or not text:
        return None
    if any(text.startswith(n) or n in text for n in CONSOLE_NOISE):
        return None
    first = text.strip().splitlines()[0][:120]
    return Finding(kind="console_error", severity=Severity.MEDIUM,
                   title=f"console.error on {_path(url)}: {first}", url=url, evidence=text[:2000])


def detect_page_error(message: str, url: str, stack: str = "") -> Finding | None:
    if not message:
        return None
    first = message.strip().splitlines()[0][:120]
    return Finding(kind="js_exception", severity=Severity.HIGH,
                   title=f"Uncaught JS exception on {_path(url)}: {first}", url=url,
                   evidence=(stack or message)[:2000])


def detect_request_failed(url: str, method: str, failure: str,
                          resource_type: str = "") -> Finding | None:
    if not failure or any(failure.startswith(f) for f in IGNORED_FAILURES):
        return None
    if _path(url) in IGNORED_PATHS:
        return None
    return Finding(kind="request_failed", severity=Severity.LOW,
                   title=f"Request failed ({failure}) on {method} {_path(url)}", url=url,
                   method=method, evidence=failure, detail={"resource_type": resource_type})


def detect_sql_leak(body: str, url: str, method: str = "GET", status: int | None = None,
                    payload: str | None = None, param: str | None = None) -> Finding | None:
    m = find_sql_error(body)
    if not m:
        return None
    where = f" via `{param}`" if param else ""
    return Finding(
        kind="sql_error_leak", severity=Severity.CRITICAL,
        title=f"Database error leaked on {method} {_path(url)}{where} (possible SQL injection)",
        url=url, method=method, status=status, payload=payload, param=param,
        evidence=_snippet(body, m, 1200), detail={"signature": m.group(0)},
    )


def detect_traversal(body: str, url: str, payload: str | None, method: str = "GET",
                     param: str | None = None, status: int | None = None) -> Finding | None:
    if not payload or not body:
        return None
    for sig in TRAVERSAL_SIGNATURES:
        m = sig.search(body)
        if m:
            return Finding(
                kind="path_traversal", severity=Severity.CRITICAL,
                title=f"Path traversal on {method} {_path(url)} via `{param or 'input'}`",
                url=url, method=method, status=status, payload=payload, param=param,
                evidence=_snippet(body, m, 800), detail={"signature": m.group(0)},
            )
    return None


def is_marker_reflected(body: str, marker: str) -> bool:
    """True when the marker appears as a raw (unescaped) HTML tag."""
    if not body or not marker:
        return False
    return re.search(rf"<{re.escape(marker)}[\s>/]", body, re.IGNORECASE) is not None


def detect_reflected_xss(url: str, marker: str, payload: str, *, body: str = "",
                         dom_hit: bool = False, dialog_hit: bool = False, method: str = "GET",
                         param: str | None = None) -> Finding | None:
    raw = is_marker_reflected(body, marker)
    if not (raw or dom_hit or dialog_hit):
        return None
    how = []
    if dialog_hit:
        how.append("payload executed (alert fired)")
    if dom_hit:
        how.append("injected element present in DOM")
    if raw:
        how.append("payload reflected unescaped in HTML")
    evidence = ""
    if raw:
        m = re.search(rf"<{re.escape(marker)}", body, re.IGNORECASE)
        evidence = _snippet(body, m, 400)
    return Finding(
        kind="reflected_xss", severity=Severity.HIGH,
        title=f"Reflected XSS on {method} {_path(url)} via `{param or 'input'}`",
        url=url, method=method, payload=payload, param=param,
        evidence=evidence or "; ".join(how), detail={"how": how, "marker": marker},
    )


def detect_double_submit(posts: Iterable[dict], window_s: float = 1.5) -> Finding | None:
    """``posts``: dicts with ts, url, method, body_hash, status (status may be None if aborted).

    Flags 2+ identical non-GET requests sent within ``window_s`` where the server accepted at
    least one (2xx/3xx), i.e. nothing in the UI prevented the duplicate submission.
    """
    items = sorted(posts, key=lambda p: p["ts"])
    for i, first in enumerate(items):
        same = [first]
        for other in items[i + 1:]:
            if other["ts"] - first["ts"] > window_s:
                break
            if (other["url"], other["method"], other["body_hash"]) == (
                    first["url"], first["method"], first["body_hash"]):
                same.append(other)
        accepted = [p for p in same if p.get("status") and 200 <= p["status"] < 400]
        if len(same) >= 2 and len(accepted) >= 2:
            return Finding(
                kind="double_submit", severity=Severity.MEDIUM,
                title=f"Duplicate submission accepted on {first['method']} {_path(first['url'])}",
                url=first["url"], method=first["method"], status=accepted[-1]["status"],
                evidence=(f"{len(same)} identical {first['method']} requests sent within "
                          f"{window_s}s; {len(accepted)} accepted by the server. The UI does not "
                          "disable the submit control and the endpoint is not idempotent."),
                detail={"count": len(same), "accepted": len(accepted)},
            )
    return None
