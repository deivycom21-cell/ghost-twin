import pytest

from ghost_twin.config import RunConfig, parse_duration
from ghost_twin.safety import (
    ScopeError,
    ScopeGuard,
    is_destructive_label,
    is_destructive_url,
    is_local_host,
)


@pytest.mark.parametrize("text,expected", [
    ("60s", 60), ("2m", 120), ("1h30m", 5400), ("500ms", 0.5), ("45", 45), (" 1m 30s ", 90),
    (30, 30), (1.5, 1.5),
])
def test_parse_duration(text, expected):
    assert parse_duration(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "abc", "10x", "-5s", "0s", "1h-2m", 0, -3])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_runconfig_validate_defaults_and_scheme():
    cfg = RunConfig(target="localhost:3000").validate()
    assert cfg.target == "http://localhost:3000"
    assert cfg.personas == ["normal_user", "chaos_gremlin", "security_prober"]


@pytest.mark.parametrize("kwargs", [
    {"users": 0}, {"max_browsers": 0}, {"video": "always"}, {"ai": "gpt5"},
    {"personas": ["hacker"]}, {"personas": []}, {"max_steps": 0},
])
def test_runconfig_rejects(kwargs):
    with pytest.raises(ValueError):
        RunConfig(target="http://localhost", **kwargs).validate()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.8.8.8", "[::1]", "::1",
                                  "192.168.1.20", "10.0.0.5", "172.16.3.4", "app.localhost",
                                  "myapp.test", "printer.local"])
def test_local_hosts_allowed(host):
    assert is_local_host(host)


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "google.com", "", None,
                                  "localhost.evil.com"])
def test_remote_hosts_rejected(host):
    assert not is_local_host(host)


def test_scope_guard_requires_allow_remote():
    with pytest.raises(ScopeError):
        ScopeGuard("https://example.com").check_target()
    ScopeGuard("https://example.com", allow_remote=True).check_target()
    ScopeGuard("http://localhost:3000").check_target()


def test_same_origin():
    g = ScopeGuard("http://localhost:3000/app")
    assert g.same_origin("http://localhost:3000/other?x=1")
    assert not g.same_origin("http://localhost:3001/")
    assert not g.same_origin("https://localhost:3000/")
    assert not g.same_origin("http://127.0.0.1:3000/")
    assert not g.same_origin("javascript:alert(1)")
    assert ScopeGuard("http://localhost").same_origin("http://localhost:80/x")


@pytest.mark.parametrize("label,expected", [
    ("Eliminar cuenta", True), ("Delete", True), ("Log out", True), ("Cerrar sesión", True),
    ("Pay now", True), ("Enviar", False), ("Payment details", False), ("Search", False),
    ("PayPal info", False), ("Create account", False), ("", False), (None, False),
])
def test_destructive_labels(label, expected):
    assert is_destructive_label(label) is expected


def test_destructive_urls():
    assert is_destructive_url("http://x/logout")
    assert is_destructive_url("http://x/users/3/delete")
    assert not is_destructive_url("http://x/deleted-items-report")
    assert not is_destructive_url("http://x/contact")
