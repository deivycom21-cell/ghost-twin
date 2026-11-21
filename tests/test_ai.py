"""AI providers are tested with fakes only — zero tokens spent."""

import json
import subprocess

import httpx
import pytest

from ghost_twin.ai import (
    AIError,
    Anthropic,
    CachedProvider,
    ClaudeCLI,
    Ollama,
    OpenAICompat,
    get_provider,
)
from ghost_twin.ai.tasks import extract_json, suggest_form_values, summarize_bugs
from ghost_twin.models import FieldValue
from ghost_twin.personas.datagen import Identity


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_claude_cli_uses_stdin_and_parses_stdout():
    calls = {}

    def fake_run(args, **kw):
        calls["args"], calls["input"] = args, kw["input"]
        return subprocess.CompletedProcess(args, 0, stdout=" hello \n", stderr="")

    p = ClaudeCLI(binary="claude", runner=fake_run)
    assert p.complete("question", system="sys") == "hello"
    assert calls["args"][:2] == ["claude", "-p"]
    assert "question" in calls["input"] and "sys" in calls["input"]


def test_claude_cli_errors():
    def failing(args, **kw):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="not logged in")

    with pytest.raises(AIError, match="not logged in"):
        ClaudeCLI(binary="claude", runner=failing).complete("x")

    def missing(args, **kw):
        raise FileNotFoundError("claude")

    with pytest.raises(AIError):
        ClaudeCLI(binary="claude", runner=missing).complete("x")


def test_ollama():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/api/generate" and body["stream"] is False
        assert body["model"] == "qwen2.5"
        return httpx.Response(200, json={"response": "ok!"})

    p = Ollama(host="http://127.0.0.1:11434", model="qwen2.5", client=mock_client(handler))
    assert p.complete("hi") == "ok!"


def test_anthropic_byok(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def handler(request):
        assert request.headers["x-api-key"] == "sk-test"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["system"] == "sys" and body["messages"][0]["content"] == "hi"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "yo"}]})

    assert Anthropic(model="m", client=mock_client(handler)).complete("hi", "sys") == "yo"


def test_anthropic_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AIError):
        Anthropic()


def test_openai_compat_and_http_errors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")

    def ok(request):
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": "fine"}}]})

    assert OpenAICompat(client=mock_client(ok)).complete("x") == "fine"

    def boom(request):
        return httpx.Response(429, text="rate limited")

    with pytest.raises(AIError, match="429"):
        OpenAICompat(client=mock_client(boom)).complete("x")


def test_openai_local_server_needs_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    OpenAICompat(base_url="http://localhost:1234/v1")


class Counting:
    name, model = "fake", "m"

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def complete(self, prompt, system="", max_tokens=1200):
        self.calls += 1
        return self.reply


def test_cache_avoids_second_call(store):
    inner = Counting("answer")
    p = CachedProvider(inner, store)
    assert p.complete("q") == "answer" and p.complete("q") == "answer"
    assert inner.calls == 1
    p.complete("other")
    assert inner.calls == 2


def test_get_provider():
    assert get_provider("none") is None
    with pytest.raises(AIError):
        get_provider("skynet")


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('Sure!\n```json\n{"a": "b"}\n```', {"a": "b"}),
    ('[1, 2]', [1, 2]),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_invalid():
    with pytest.raises(AIError):
        extract_json("no json here")


def test_suggest_form_values():
    import random

    p = Counting('{"email": "ana@example.com", "name": "Ana", "nested": {"x": 1}}')
    fields = [FieldValue("#e", "x", "email", name="email"), FieldValue("#n", "x", name="name")]
    out = suggest_form_values(p, fields, Identity.generate(random.Random(1)))
    assert out == {"email": "ana@example.com", "name": "Ana"}


def test_summarize_bugs_prompt_language(clock_store):
    from ghost_twin.reporter import group_findings
    from tests.test_reporter import populate

    run = populate(clock_store)
    seen = {}

    class Spy(Counting):
        def complete(self, prompt, system="", max_tokens=1200):
            seen["prompt"] = prompt
            return "- summary"

    assert summarize_bugs(Spy(""), "http://x", group_findings(clock_store.findings(run)), "es")
    assert "Spanish" in seen["prompt"] and "BUG-001" in seen["prompt"]


def test_report_ai_section_and_fallback(clock_store, tmp_path):
    from ghost_twin.reporter import build_report
    from tests.test_reporter import populate

    run = populate(clock_store)
    text = build_report(clock_store, run, data_dir=tmp_path, ai=Counting("**Root cause:** no validation"))
    assert "## AI analysis" in text and "no validation" in text

    class Broken(Counting):
        def complete(self, *a, **k):
            raise AIError("down")

    assert "## AI analysis" not in build_report(clock_store, run, data_dir=tmp_path, ai=Broken(""))
