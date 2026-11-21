"""A deliberately broken web app used by the test-suite and by ``ghost-twin demo``.

Standard library only. Every bug here is planted on purpose — see PLAN.md, phase 3.
NEVER expose this server outside localhost.
"""

from __future__ import annotations

import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

FAKE_FILES = {
    "readme.txt": "Welcome to Buggy Shop! Files are served from /srv/files.",
    "terms.txt": "Be nice.",
}
FAKE_PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
    "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n"
)
SQL_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "app.py", line 42, in search\n'
    "    cur.execute(f\"SELECT * FROM products WHERE name LIKE '%{q}%'\")\n"
    'sqlite3.OperationalError: unrecognized token: "\'"\n'
)

STYLE = (
    "<style>body{font-family:system-ui;max-width:760px;margin:2rem auto;padding:0 1rem}"
    "nav a{margin-right:.8rem}form{display:grid;gap:.5rem;max-width:420px}</style>"
)
NAV = (
    '<nav><a href="/">Home</a><a href="/search?q=shoes">Search</a>'
    '<a href="/file?name=readme.txt">Readme</a><a href="/contact">Contact</a>'
    '<a href="/signup">Sign up</a><a href="/checkout">Checkout</a><a href="/about">About</a>'
    '<a href="/slow">Deals</a><a href="/broken-js">Widgets</a></nav>'
)


def page(title: str, body: str, script: str = "") -> bytes:
    s = f"<script>{script}</script>" if script else ""
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>{STYLE}</head>"
        f"<body>{NAV}<h1>{title}</h1>{body}{s}</body></html>"
    ).encode()


OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Buggy Shop API", "version": "1.0.0"},
    "paths": {
        "/api/items": {
            "get": {"summary": "List items", "parameters": [
                {"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "ok"}}},
            "post": {"summary": "Create item", "requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/NewItem"}}}},
                "responses": {"201": {"description": "created"}}},
        },
        "/api/items/{item_id}": {
            "get": {"summary": "Get item", "parameters": [
                {"name": "item_id", "in": "path", "required": True,
                 "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "ok"}}},
        },
    },
    "components": {"schemas": {"NewItem": {
        "type": "object", "required": ["name", "price"],
        "properties": {"name": {"type": "string"}, "price": {"type": "number"}}}}},
}


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        """Clients (chaos gremlins!) closing sockets mid-request is expected — stay quiet."""


class BuggyApp:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, slow_seconds: float = 2.5):
        self.host, self.port, self.slow_seconds = host, port, slow_seconds
        self.signups: list[dict] = []
        self.items = [{"id": 1, "name": "Shoes", "price": 49.9}, {"id": 2, "name": "Hat", "price": 15}]
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.crashed = False
        self._stopped = False

    @property
    def url(self) -> str:
        assert self.server is not None
        return f"http://{self.host}:{self.server.server_address[1]}"

    def start(self) -> BuggyApp:
        app = self

        class Handler(_Handler):
            pass

        Handler.app = app
        self.server = _QuietServer((self.host, self.port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.crashed = True  # open keep-alive connections stop being served too
        if self.server and not self._stopped:
            self._stopped = True
            self.server.shutdown()
            self.server.server_close()

    def crash(self) -> None:
        self.crashed = True
        threading.Thread(target=self.stop, daemon=True).start()

    def __enter__(self) -> BuggyApp:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


class _Handler(BaseHTTPRequestHandler):
    app: BuggyApp
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep test output clean
        pass

    def handle_one_request(self) -> None:
        if self.app.crashed:  # a dead process doesn't answer on old sockets either
            self.close_connection = True
            return
        super().handle_one_request()

    # ------------------------------------------------------------------ helpers
    def send(self, status: int, body: bytes | str, ctype: str = "text/html; charset=utf-8") -> None:
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status: int, obj) -> None:
        self.send(status, json.dumps(obj), "application/json")

    def read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    # ------------------------------------------------------------------ routes
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path, qs = parts.path, parse_qs(parts.query, keep_blank_values=True)
        q = lambda k, d="": qs.get(k, [d])[0]  # noqa: E731

        if path == "/":
            self.send(200, page(
                "Buggy Shop",
                "<p>The best (worst) shop on localhost.</p>"
                '<form action="/search" method="get"><input name="q" placeholder="Search products">'
                "<button type=submit>Search</button></form>",
                script="console.error('Failed to init analytics: missing ANALYTICS_KEY');",
            ))
        elif path == "/search":
            term = q("q")
            if "'" in term:
                # BUG: string-built SQL + traceback leaked to the client
                self.send(500, f"<h1>Internal Server Error</h1><pre>{html.escape(SQL_TRACEBACK)}</pre>")
                return
            # BUG: reflected without escaping
            self.send(200, page("Search", f'<form action="/search"><input name="q" value="">'
                                          f"<button>Go</button></form><p>Results for: {term}</p>"
                                          "<ul><li>Shoes</li><li>Hat</li></ul>"))
        elif path == "/file":
            name = q("name")
            if ".." in name or name.startswith("/"):
                self.send(200, f"<pre>{FAKE_PASSWD}</pre>")  # BUG: path traversal
            elif name in FAKE_FILES:
                self.send(200, page(name, f"<pre>{html.escape(FAKE_FILES[name])}</pre>"))
            else:
                self.send(404, page("Not found", "<p>No such file.</p>"))
        elif path == "/contact":
            self.send(200, page("Contact", (
                '<form action="/contact" method="post">'
                '<label for=n>Name</label><input id=n name="name" required>'
                '<label for=e>Email</label><input id=e name="email" type="email" required>'
                '<label for=m>Message</label><textarea id=m name="message" maxlength="2000" required>'
                "</textarea><button type=submit>Send</button></form>")))
        elif path == "/signup":
            self.send(200, page("Sign up", (
                '<form id="signup"><label for=u>Email</label><input id=u name="email" type="email">'
                '<label for=p>Password</label><input id=p name="password" type="password">'
                '<label for=a>Age</label><input id=a name="age" type="number" min="13" max="120">'
                '<button type="submit">Create account</button></form><p id="out"></p>'),
                script=(
                    "document.getElementById('signup').addEventListener('submit', async e => {"
                    "e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));"
                    "const r = await fetch('/api/signup', {method:'POST', headers:{'Content-Type':"
                    "'application/json'}, body: JSON.stringify(d)});"
                    "document.getElementById('out').textContent = r.ok ? 'Welcome!' : 'Error';});"
                )))
        elif path == "/checkout":
            self.send(200, page("Checkout", '<p id="cart">Loading cart…</p>', script=(
                "fetch('/api/cart').then(r => r.json()).then(c => {"
                "document.getElementById('cart').textContent = c.items.length + ' items'; });")))
        elif path == "/about":
            self.send(200, page("About", "<p>Made with bugs.</p>"))
        elif path == "/slow":
            time.sleep(self.app.slow_seconds)
            self.send(200, page("Deals", "<p>Today's deals took a while…</p>"))
        elif path == "/broken-js":
            self.send(200, page("Widgets", "<div id=w></div>",
                                script="window.renderWidgets(document.getElementById('w'));"))
        elif path == "/crash":
            self.send(200, "bye")
            self.app.crash()
        elif path == "/openapi.json":
            self.send_json(200, OPENAPI)
        elif path == "/api/cart":
            self.send_json(500, {"error": "TypeError: Cannot read properties of null (reading 'items')"})
        elif path == "/api/items":
            self.send_json(200, self.app.items[: int(q("limit", "50") or 50)]
                           if q("limit", "50").lstrip("-").isdigit() else self.app.items)
        elif path.startswith("/api/items/"):
            raw = path.rsplit("/", 1)[1]
            try:
                item_id = int(raw)  # BUG: no validation -> unhandled ValueError -> 500
            except ValueError:
                self.send(500, f"Traceback (most recent call last):\nValueError: invalid literal for "
                               f"int() with base 10: {raw!r}", "text/plain")
                return
            item = next((i for i in self.app.items if i["id"] == item_id), None)
            self.send_json(200 if item else 404, item or {"error": "not found"})
        else:
            self.send(404, page("Not found", "<p>404</p>"))

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        raw = self.read_body()
        if path == "/contact":
            form = parse_qs(raw.decode(errors="replace"), keep_blank_values=True)
            msg = form.get("message", [""])[0]
            if not msg.strip() or len(msg) > 5000:
                # BUG: missing server-side validation
                self.send(500, "<h1>500</h1><pre>IndexError: string index out of range</pre>")
                return
            self.send(200, page("Thanks", "<p>We got your message.</p>"))
        elif path == "/api/signup":
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                self.send_json(400, {"error": "bad json"})
                return
            # BUG: not idempotent, no duplicate check
            self.app.signups.append(data)
            self.send_json(201, {"ok": True, "user_id": len(self.app.signups)})
        elif path == "/api/items":
            try:
                data = json.loads(raw or b"{}")
                item = {"id": len(self.app.items) + 1, "name": data["name"],
                        "price": round(float(data["price"]) * 1.0, 2)}
                if not isinstance(data["name"], str):
                    raise TypeError("name must be str")
            except (KeyError, TypeError, ValueError) as exc:  # BUG: 500 instead of 422
                self.send(500, f"Traceback (most recent call last):\n{type(exc).__name__}: {exc}",
                          "text/plain")
                return
            self.app.items.append(item)
            self.send_json(201, item)
        else:
            self.send(404, page("Not found", "<p>404</p>"))


def serve(host: str = "127.0.0.1", port: int = 3000) -> None:
    app = BuggyApp(host, port).start()
    print(f"Buggy demo app running at {app.url}  (Ctrl+C to stop)")
    try:
        while not app.crashed:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
