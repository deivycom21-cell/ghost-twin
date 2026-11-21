"""Persona decisions are pure functions of (snapshot, seed): no browser needed."""

import random

import pytest

from ghost_twin.models import ButtonInfo, FormInfo, InputInfo, LinkInfo, PageSnapshot
from ghost_twin.personas import PERSONAS, AgentContext, assign_personas, create_persona
from ghost_twin.personas.datagen import CHAOS_STRATEGIES, Identity, chaos_value, valid_value
from ghost_twin.personas.payloads import SQLI_AGGRESSIVE, payloads_for
from ghost_twin.personas.security_prober import with_param
from ghost_twin.safety import ScopeGuard

BASE = "http://localhost:3000"


def snap(url=BASE + "/"):
    return PageSnapshot(
        url=url, title="Shop",
        links=[
            LinkInfo(BASE + "/search?q=shoes", "Search", '[data-gt-id="gt1"]'),
            LinkInfo(BASE + "/file?name=readme.txt", "Readme", '[data-gt-id="gt2"]'),
            LinkInfo(BASE + "/logout", "Log out", '[data-gt-id="gt3"]'),
            LinkInfo("https://evil.example.com/", "Partner", '[data-gt-id="gt4"]'),
            LinkInfo(BASE + "/about", "About", '[data-gt-id="gt5"]'),
        ],
        forms=[
            FormInfo('[data-gt-id="gt6"]', BASE + "/contact", "post", [
                InputInfo('[data-gt-id="gt7"]', "name", "text", label="Name", required=True),
                InputInfo('[data-gt-id="gt8"]', "email", "email", label="Email"),
                InputInfo('[data-gt-id="gt9"]', "age", "number", min="18", max="99"),
                InputInfo('[data-gt-id="gt10"]', "message", "textarea", tag="textarea"),
            ], '[data-gt-id="gt11"]', "Send"),
            FormInfo('[data-gt-id="gt12"]', BASE + "/account/delete", "post", [
                InputInfo('[data-gt-id="gt13"]', "confirm", "text")], '[data-gt-id="gt14"]',
                "Delete account"),
        ],
        buttons=[ButtonInfo('[data-gt-id="gt15"]', "Load more"),
                 ButtonInfo('[data-gt-id="gt16"]', "Eliminar todo")],
    )


def ctx(seed=42, **kw):
    return AgentContext(start_url=BASE, guard=ScopeGuard(BASE), seed=seed, **kw)


def run_plan(name, seed=42, steps=25, **kw):
    p = create_persona(name, ctx(seed, **kw))
    return [p.next_action(snap()) for _ in range(steps)]


def flat(actions):
    return [(a.kind, a.target, a.mode, tuple((f.name, f.value) for f in a.fields)) for a in actions]


@pytest.mark.parametrize("name", list(PERSONAS))
def test_same_seed_same_plan(name):
    assert flat(run_plan(name, 7)) == flat(run_plan(name, 7))


@pytest.mark.parametrize("name", list(PERSONAS))
def test_different_seed_different_plan(name):
    assert flat(run_plan(name, 1, 40)) != flat(run_plan(name, 2, 40))


@pytest.mark.parametrize("name", list(PERSONAS))
def test_never_destructive_or_offsite(name):
    for a in run_plan(name, steps=60):
        assert a.target not in {'[data-gt-id="gt3"]', '[data-gt-id="gt4"]', '[data-gt-id="gt16"]'}
        assert a.form_selector != '[data-gt-id="gt12"]'
        if a.kind == "goto":
            assert a.target.startswith(BASE)
            assert "logout" not in a.target


def test_allow_destructive_unlocks_forms():
    p = create_persona("chaos_gremlin", ctx(allow_destructive=True))
    used = {p.next_action(snap()).form_selector for _ in range(60)}
    assert '[data-gt-id="gt12"]' in used


def test_normal_user_fills_valid_data():
    p = create_persona("normal_user", ctx())
    submits = [a for a in (p.next_action(snap()) for _ in range(10)) if a.kind == "submit_form"]
    assert submits, "normal_user should submit the contact form"
    values = {f.name: f.value for f in submits[0].fields}
    assert "@" in values["email"] and "." in values["email"].split("@")[1]
    assert 18 <= int(values["age"]) <= 99
    assert values["name"] and len(values["message"]) > 10
    assert submits[0].mode == "click" and not submits[0].bypass_validation
    assert len(submits) == 1, "a form is submitted once per normal_user"


def test_gremlin_double_submits_then_abuses():
    p = create_persona("chaos_gremlin", ctx())
    actions = [p.next_action(snap()) for _ in range(30)]
    submits = [a for a in actions if a.kind == "submit_form"]
    assert submits[0].mode == "dblclick" and submits[0].bypass_validation
    notes = {a.note for a in submits}
    assert any(n.startswith("chaos:") for n in notes)
    kinds = {a.kind for a in actions}
    assert kinds & {"reload", "back", "throttle", "dblclick", "close_tab"}


def test_prober_payloads_and_targets():
    p = create_persona("security_prober", ctx())
    actions = [p.next_action(snap()) for _ in range(40)]
    probes = [a for a in actions if a.payload]
    kinds = {a.payload_kind for a in probes}
    assert {"sqli", "xss", "traversal"} <= kinds
    traversal_params = {a.param for a in probes if a.payload_kind == "traversal"}
    assert "name" in traversal_params  # /file?name= looks like a path
    assert not any(a.payload in SQLI_AGGRESSIVE for a in probes)
    for a in probes:
        if a.payload_kind == "xss":
            assert a.marker and a.marker in a.payload
    url_probe = next(a for a in probes if a.kind == "goto" and a.param == "q")
    assert url_probe.target.startswith(BASE + "/search?q=")


def test_prober_aggressive_adds_tautologies():
    p = create_persona("security_prober", ctx(aggressive=True))
    payloads = {a.payload for a in (p.next_action(snap()) for _ in range(60)) if a.payload}
    assert payloads & set(SQLI_AGGRESSIVE)


def test_payloads_for_and_with_param():
    rng = random.Random(0)
    kinds = [p.kind for p in payloads_for("q", "shoes", rng)]
    assert "traversal" not in kinds and "sqli" in kinds and "xss" in kinds
    assert "traversal" in [p.kind for p in payloads_for("file", "a.txt", rng)]
    assert with_param("http://h/s?q=a&x=1", "q", "'") == "http://h/s?q=%27&x=1"


@pytest.mark.parametrize("inp,check", [
    (InputInfo("s", "email", "email"), lambda v: "@" in v),
    (InputInfo("s", "user_age", "number", min="13", max="20"), lambda v: 13 <= int(v) <= 20),
    (InputInfo("s", "phone", "tel"), lambda v: v.startswith("+")),
    (InputInfo("s", "dob", "date"), lambda v: len(v) == 10 and v[4] == "-"),
    (InputInfo("s", "country", "select", tag="select", options=["", "co", "es"]),
     lambda v: v in {"co", "es"}),
    (InputInfo("s", "message", "textarea", tag="textarea"), lambda v: len(v) > 10),
    (InputInfo("s", "q", "search"), lambda v: v.isalpha()),
    (InputInfo("s", "password", "password"), lambda v: len(v) >= 8),
])
def test_valid_values(inp, check):
    rng = random.Random(3)
    assert check(valid_value(inp, rng, Identity.generate(rng)))


def test_chaos_values():
    rng = random.Random(1)
    inp = InputInfo("s", "qty", "number")
    assert chaos_value(inp, rng, "empty") == ""
    assert len(chaos_value(inp, rng, "huge")) > 10_000
    assert chaos_value(InputInfo("s", "email", "email"), rng, "type_confusion").count("@") != 1 \
        or "." not in chaos_value(InputInfo("s", "email", "email"), random.Random(5), "type_confusion")
    for s in CHAOS_STRATEGIES:
        assert isinstance(chaos_value(inp, rng, s), str)


def test_assign_personas_round_robin():
    assert assign_personas(4, ["a", "b", "c"]) == ["a", "b", "c", "a"]
