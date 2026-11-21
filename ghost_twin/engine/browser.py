"""Low-memory Chromium launch + per-agent contexts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ghost_twin.config import RunConfig

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Browser, BrowserContext, Playwright, Route

LOW_MEMORY_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-translate",
    "--no-first-run",
    "--mute-audio",
    "--metrics-recording-only",
    "--disable-component-update",
]
HEAVY_RESOURCES = {"image", "font", "media"}


async def launch_browser(pw: Playwright, headless: bool = True) -> Browser:
    return await pw.chromium.launch(headless=headless, args=LOW_MEMORY_ARGS)


async def _block_heavy(route: Route) -> None:
    if route.request.resource_type in HEAVY_RESOURCES:
        await route.abort("blockedbyclient")
    else:
        await route.continue_()


async def new_context(browser: Browser, cfg: RunConfig,
                      video_dir: Path | None = None) -> BrowserContext:
    kwargs: dict = {
        "viewport": {"width": 1280, "height": 720},
        "ignore_https_errors": True,
        "locale": "en-US",
    }
    if video_dir is not None:
        kwargs["record_video_dir"] = str(video_dir)
        kwargs["record_video_size"] = {"width": 800, "height": 450}
    ctx = await browser.new_context(**kwargs)
    ctx.set_default_timeout(cfg.action_timeout_ms)
    ctx.set_default_navigation_timeout(cfg.nav_timeout_ms)
    if cfg.lite:
        await ctx.route("**/*", _block_heavy)
    return ctx
