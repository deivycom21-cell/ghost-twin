"""Guard rails: ghost-twin only attacks your own local apps unless told otherwise."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urljoin, urlsplit

LOCAL_SUFFIXES = (".localhost", ".local", ".test", ".internal", ".lan", ".home.arpa")

DESTRUCTIVE_WORDS = [
    "delete", "remove", "destroy", "drop", "erase", "purge", "wipe", "logout", "log out",
    "sign out", "signout", "unsubscribe", "deactivate", "close account", "cancel subscription",
    "pay", "purchase", "buy now", "place order", "checkout now", "transfer", "withdraw",
    "eliminar", "borrar", "quitar", "destruir", "cerrar sesión", "cerrar sesion", "salir",
    "darse de baja", "desactivar", "pagar", "comprar ahora", "transferir", "retirar",
]
_DESTRUCTIVE_RE = re.compile(
    r"(?<![a-záéíóúñ])(" + "|".join(re.escape(w) for w in DESTRUCTIVE_WORDS) + r")(?![a-záéíóúñ])",
    re.IGNORECASE,
)
_DESTRUCTIVE_PATH_RE = re.compile(r"/(logout|signout|sign-out|log-out|delete|destroy|remove)\b", re.I)


class ScopeError(ValueError):
    """Raised when a target is outside the allowed scope."""


def is_local_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip("[]").lower().rstrip(".")
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = (parts.scheme or "http").lower()
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, (parts.hostname or "").lower(), port


def is_destructive_label(text: str | None) -> bool:
    return bool(text and _DESTRUCTIVE_RE.search(text))


def is_destructive_url(url: str | None) -> bool:
    return bool(url and _DESTRUCTIVE_PATH_RE.search(urlsplit(url).path))


class ScopeGuard:
    def __init__(self, target: str, allow_remote: bool = False):
        if not target.startswith(("http://", "https://")):
            target = "http://" + target
        self.target = target
        self.allow_remote = allow_remote
        self.origin = _origin(target)

    def check_target(self) -> None:
        parts = urlsplit(self.target)
        if parts.scheme not in {"http", "https"}:
            raise ScopeError(f"unsupported scheme: {parts.scheme!r}")
        if not parts.hostname:
            raise ScopeError(f"invalid target URL: {self.target!r}")
        if not self.allow_remote and not is_local_host(parts.hostname):
            raise ScopeError(
                f"{parts.hostname} is not a local/private host. ghost-twin only attacks apps you "
                "run yourself. If you own this host and are authorised to test it, pass "
                "--allow-remote."
            )

    def same_origin(self, url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return False
        return _origin(url) == self.origin

    def resolve(self, href: str, base: str | None = None) -> str:
        return urljoin(base or self.target, href)
