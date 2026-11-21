"""What the AI is used for. Each task has a strict output format and a deterministic fallback
in the caller: AI never decides security payloads."""

from __future__ import annotations

import json
import re
from typing import Any

from ghost_twin.ai.providers import AIError, AIProvider

FORM_SYSTEM = (
    "You generate realistic test data for web forms. Reply with ONE JSON object mapping each "
    "field name to a string value. No prose, no markdown."
)
SUMMARY_SYSTEM = (
    "You are a senior engineer triaging bugs found by an automated QA tool. Be concrete and "
    "brief. Use Markdown bullet points. Do not invent facts that are not in the input."
)


def extract_json(text: str) -> Any:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise AIError("no JSON found in AI output")
    end = max(text.rfind("}"), text.rfind("]"))
    try:
        return json.loads(text[start:end + 1])
    except ValueError as exc:
        raise AIError("invalid JSON in AI output") from exc


def suggest_form_values(provider: AIProvider, fields, identity) -> dict[str, str]:
    """Ask for realistic values for ``fields`` (``FieldValue`` list) as persona ``identity``."""
    described = [{"name": f.name or f.selector, "type": f.type, "tag": f.tag} for f in fields
                 if f.type not in {"checkbox", "radio", "hidden"}]
    prompt = (
        f"Persona: {identity.first} {identity.last}, {identity.age} years old, from "
        f"{identity.city}, {identity.country}, email {identity.email}.\n"
        f"Fields: {json.dumps(described, ensure_ascii=False)}\n"
        "Return valid, realistic values this person would type. Keep emails/phones consistent "
        "with the persona. Dates as YYYY-MM-DD."
    )
    data = extract_json(provider.complete(prompt, FORM_SYSTEM, max_tokens=500))
    if not isinstance(data, dict):
        raise AIError("expected a JSON object")
    return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}


def summarize_bugs(provider: AIProvider, target: str, bugs, lang: str = "en") -> str:
    compact = [{
        "id": f"BUG-{i:03d}", "severity": b.severity.value, "kind": b.first["kind"],
        "title": b.first["title"], "url": b.first["url"], "payload": b.first.get("payload"),
        "evidence": (b.first.get("evidence") or "")[:400], "hits": len(b.occurrences),
    } for i, b in enumerate(bugs[:25], 1)]
    language = "Spanish" if lang == "es" else "English"
    prompt = (
        f"Target app: {target}\nBugs (JSON): {json.dumps(compact, ensure_ascii=False)}\n\n"
        f"Write in {language}: (1) a 2-3 sentence executive summary, (2) likely shared root "
        "causes grouping bug IDs, (3) the recommended fix order. Max 200 words."
    )
    return provider.complete(prompt, SUMMARY_SYSTEM, max_tokens=700)
