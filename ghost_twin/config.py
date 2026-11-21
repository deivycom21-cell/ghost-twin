"""Run configuration and small parsing helpers."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PERSONAS = ["normal_user", "chaos_gremlin", "security_prober"]
VIDEO_MODES = {"on-failure", "off"}
AI_BACKENDS = {"none", "claude", "ollama", "anthropic", "openai"}

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)?", re.IGNORECASE)


def parse_duration(value: str | int | float) -> float:
    """Parse ``"60s"``, ``"2m"``, ``"1h30m"``, ``"500ms"`` or a bare number (seconds)."""
    if isinstance(value, (int, float)):
        if value <= 0:
            raise ValueError("duration must be positive")
        return float(value)
    text = value.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("empty duration")
    pos, total = 0, 0.0
    for m in _DURATION_RE.finditer(text):
        if m.start() != pos:
            raise ValueError(f"invalid duration: {value!r}")
        num, unit = float(m.group(1)), (m.group(2) or "s")
        total += num * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
        pos = m.end()
    if pos != len(text):
        raise ValueError(f"invalid duration: {value!r}")
    if total <= 0:
        raise ValueError("duration must be positive")
    return total


@dataclass
class RunConfig:
    target: str
    users: int = 3
    duration: float = 60.0
    max_steps: int | None = None
    max_browsers: int = 2
    personas: list[str] = field(default_factory=lambda: list(DEFAULT_PERSONAS))
    seed: int = 1337
    headless: bool = True
    video: str = "on-failure"
    screenshots: bool = True
    slow_ms: int = 2000
    lite: bool = False
    allow_remote: bool = False
    aggressive: bool = False
    allow_destructive: bool = False
    data_dir: Path = Path(".ghost-twin")
    ai: str = "none"
    api: bool = True
    openapi: str | None = None
    step_delay_ms: int = 150
    action_timeout_ms: int = 5000
    nav_timeout_ms: int = 15000
    health_interval: float = 2.0
    max_api_requests: int = 150

    def validate(self) -> RunConfig:
        from ghost_twin.personas.registry import PERSONAS

        if self.users < 1:
            raise ValueError("--users must be >= 1")
        if self.max_browsers < 1:
            raise ValueError("--max-browsers must be >= 1")
        if self.duration <= 0:
            raise ValueError("--duration must be positive")
        if self.max_steps is not None and self.max_steps < 1:
            raise ValueError("--max-steps must be >= 1")
        if self.video not in VIDEO_MODES:
            raise ValueError(f"--video must be one of {sorted(VIDEO_MODES)}")
        if self.ai not in AI_BACKENDS:
            raise ValueError(f"--ai must be one of {sorted(AI_BACKENDS)}")
        if not self.personas:
            raise ValueError("at least one persona is required")
        unknown = [p for p in self.personas if p not in PERSONAS]
        if unknown:
            raise ValueError(f"unknown persona(s): {', '.join(unknown)}; known: {', '.join(PERSONAS)}")
        if not self.target.startswith(("http://", "https://")):
            self.target = "http://" + self.target
        self.data_dir = Path(self.data_dir)
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["data_dir"] = str(self.data_dir)
        return d
