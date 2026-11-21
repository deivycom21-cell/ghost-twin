"""Shared fixtures. All tests are offline: the target is the bundled buggy demo app."""

from __future__ import annotations

import contextlib
import functools
import sys
from pathlib import Path

import pytest

from ghost_twin.demo import BuggyApp
from ghost_twin.recorder.store import Store

if sys.platform == "win32":  # stdout of the test runner may be cp1252
    for _s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            _s.reconfigure(errors="replace")


@functools.lru_cache(maxsize=1)
def chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    e2e = [i for i in items if "e2e" in i.keywords]
    if e2e and not chromium_available():
        skip = pytest.mark.skip(reason="Chromium not installed (python -m playwright install chromium)")
        for item in e2e:
            item.add_marker(skip)


@pytest.fixture
def buggy_app():
    app = BuggyApp(slow_seconds=1.2).start()
    yield app
    app.stop()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "ghost.db")
    yield s
    s.close()


class FakeClock:
    def __init__(self, start: float = 1_767_225_600.0):  # 2026-01-01 00:00 UTC
        self.t = start

    def __call__(self) -> float:
        self.t += 1.0
        return self.t


@pytest.fixture
def clock_store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "ghost.db", clock=FakeClock())
    yield s
    s.close()
