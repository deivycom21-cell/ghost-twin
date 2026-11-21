"""The planted bugs behave as documented (plain urllib, no browser)."""

import json
import urllib.error
import urllib.request

import pytest


def get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_home_has_links_and_console_error(buggy_app):
    status, body = get(buggy_app.url + "/")
    assert status == 200 and 'href="/contact"' in body and "console.error" in body


def test_search_sqli_and_xss(buggy_app):
    status, body = get(buggy_app.url + "/search?q=%27")
    assert status == 500 and "sqlite3.OperationalError" in body
    status, body = get(buggy_app.url + "/search?q=%3Cgtxabc%3E")
    assert status == 200 and "<gtxabc>" in body


def test_file_traversal(buggy_app):
    assert "root:x:0:0" in get(buggy_app.url + "/file?name=../../etc/passwd")[1]
    assert get(buggy_app.url + "/file?name=readme.txt")[0] == 200
    assert get(buggy_app.url + "/file?name=nope")[0] == 404


@pytest.mark.parametrize("message,expected", [("hello", 200), ("", 500), ("x" * 6000, 500)])
def test_contact_validation_bug(buggy_app, message, expected):
    data = f"name=a&email=a@b.c&message={message}".encode()
    assert get(buggy_app.url + "/contact", data)[0] == expected


def test_signup_not_idempotent(buggy_app):
    body = json.dumps({"email": "a@b.c"}).encode()
    for _ in range(2):
        assert get(buggy_app.url + "/api/signup", body, {"Content-Type": "application/json"})[0] == 201
    assert len(buggy_app.signups) == 2


def test_api_bugs(buggy_app):
    assert get(buggy_app.url + "/api/cart")[0] == 500
    assert get(buggy_app.url + "/api/items/1")[0] == 200
    assert get(buggy_app.url + "/api/items/abc")[0] == 500
    assert get(buggy_app.url + "/api/items", b"{}", {"Content-Type": "application/json"})[0] == 500
    spec = json.loads(get(buggy_app.url + "/openapi.json")[1])
    assert "/api/items/{item_id}" in spec["paths"]


def test_crash_stops_server(buggy_app):
    get(buggy_app.url + "/crash")
    buggy_app.thread.join(timeout=5)
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(buggy_app.url + "/", timeout=2)
