"""Background health-check: detects when the target app goes down during the attack."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import httpx


async def check_reachable(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False,
                                     follow_redirects=True) as client:
            r = await client.get(url)
            return True, f"HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def health_monitor(url: str, interval: float, stop_event: asyncio.Event,
                         on_down: Callable[[str], Awaitable[None]]) -> None:
    """Poll ``url``; call ``on_down`` after 2 refused connections or 3 timeouts in a row."""
    refused = timeouts = 0
    # Fresh connection per check: a reused keep-alive socket can outlive a dead listener.
    limits = httpx.Limits(max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=10, trust_env=False, limits=limits) as client:
        while not stop_event.is_set():
            try:
                await client.get(url)
                refused = timeouts = 0
            except httpx.TimeoutException as exc:
                timeouts += 1
                if timeouts >= 3:
                    await on_down(f"{type(exc).__name__}: target stopped responding")
                    return
            except httpx.TransportError as exc:
                refused += 1
                if refused >= 2:
                    await on_down(f"{type(exc).__name__}: {exc}")
                    return
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
