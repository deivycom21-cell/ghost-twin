"""Optional AI backends. Everything works without them; they only add realism and prose.

Backends (``--ai``):
  claude     local Claude Code CLI (``claude -p``), uses your existing login
  ollama     local Ollama server (OLLAMA_HOST, OLLAMA_MODEL)
  anthropic  BYOK Anthropic API (ANTHROPIC_API_KEY, GHOST_TWIN_AI_MODEL)
  openai     BYOK any OpenAI-compatible API (OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL)
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from typing import Any

import httpx

DEFAULT_TIMEOUT = 120.0


class AIError(RuntimeError):
    pass


class AIProvider:
    name = "base"
    model = ""

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}:{self.model}>"


class ClaudeCLI(AIProvider):
    """Calls ``claude -p`` (prompt via stdin) — no API key handled by ghost-twin."""

    name = "claude"

    def __init__(self, binary: str | None = None, model: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT, runner=subprocess.run):
        self.binary = binary or shutil.which("claude") or "claude"
        self.model = model or os.environ.get("GHOST_TWIN_AI_MODEL", "")
        self.timeout = timeout
        self._run = runner

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200) -> str:
        args = [self.binary, "-p", "--output-format", "text"]
        if self.model:
            args += ["--model", self.model]
        full = f"{system}\n\n{prompt}" if system else prompt
        try:
            proc = self._run(args, input=full, capture_output=True, text=True, encoding="utf-8",
                             timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AIError(f"claude CLI failed: {exc}") from exc
        if proc.returncode != 0:
            raise AIError(f"claude CLI exited {proc.returncode}: {(proc.stderr or '')[:300]}")
        return (proc.stdout or "").strip()


class _HTTPProvider(AIProvider):
    def __init__(self, client: httpx.Client | None = None, timeout: float = DEFAULT_TIMEOUT):
        self._client = client or httpx.Client(timeout=timeout)

    def _post(self, url: str, json: dict, headers: dict | None = None) -> dict:
        try:
            r = self._client.post(url, json=json, headers=headers or {})
        except httpx.HTTPError as exc:
            raise AIError(f"{self.name}: {exc}") from exc
        if r.status_code >= 400:
            raise AIError(f"{self.name}: HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError as exc:
            raise AIError(f"{self.name}: invalid JSON response") from exc


class Ollama(_HTTPProvider):
    name = "ollama"

    def __init__(self, host: str | None = None, model: str | None = None, **kw):
        super().__init__(**kw)
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.model = model or os.environ.get("OLLAMA_MODEL") or "llama3.2"

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200) -> str:
        data = self._post(f"{self.host}/api/generate", {
            "model": self.model, "prompt": prompt, "system": system, "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.4},
        })
        return str(data.get("response", "")).strip()


class Anthropic(_HTTPProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None, **kw):
        super().__init__(**kw)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise AIError("ANTHROPIC_API_KEY is not set")
        self.model = model or os.environ.get("GHOST_TWIN_AI_MODEL") or "claude-haiku-5-5"
        self.base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL")
                         or "https://api.anthropic.com").rstrip("/")

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200) -> str:
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens,
                                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        data = self._post(f"{self.base_url}/v1/messages", body, {
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()


class OpenAICompat(_HTTPProvider):
    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None, **kw):
        super().__init__(**kw)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        local = "127.0.0.1" in self.base_url or "localhost" in self.base_url
        if not self.api_key and not local:
            raise AIError("OPENAI_API_KEY is not set")
        self.model = model or os.environ.get("OPENAI_MODEL") or os.environ.get(
            "GHOST_TWIN_AI_MODEL") or "gpt-4o-mini"

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = self._post(f"{self.base_url}/chat/completions",
                          {"model": self.model, "messages": msgs, "max_tokens": max_tokens,
                           "temperature": 0.4}, headers)
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("openai: unexpected response shape") from exc


class CachedProvider(AIProvider):
    """Caches completions in SQLite so reruns are free and reproducible."""

    def __init__(self, inner: AIProvider, store):
        self.inner, self.store = inner, store
        self.name, self.model = inner.name, inner.model

    def key(self, prompt: str, system: str, max_tokens: int) -> str:
        raw = "\x1f".join([self.name, self.model, system, prompt, str(max_tokens)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200) -> str:
        k = self.key(prompt, system, max_tokens)
        hit = self.store.cache_get(k)
        if hit is not None:
            return hit
        out = self.inner.complete(prompt, system, max_tokens)
        if out:
            self.store.cache_set(k, out)
        return out


def get_provider(name: str | None, store=None, **kw) -> AIProvider | None:
    """Build a provider for ``--ai``; ``none``/empty returns ``None``."""
    name = (name or os.environ.get("GHOST_TWIN_AI") or "none").lower()
    if name in {"none", "off", ""}:
        return None
    classes = {"claude": ClaudeCLI, "ollama": Ollama, "anthropic": Anthropic,
               "openai": OpenAICompat}
    if name not in classes:
        raise AIError(f"unknown AI backend {name!r}; choose one of {', '.join(classes)}")
    provider = classes[name](**kw)
    return CachedProvider(provider, store) if store is not None else provider
