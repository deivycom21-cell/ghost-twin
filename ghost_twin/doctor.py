"""``ghost-twin doctor``: environment checks with actionable fixes."""

from __future__ import annotations

import contextlib
import ctypes
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

REQUIRED = ["playwright", "fastapi", "uvicorn", "typer", "httpx", "rich", "websockets"]


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str
    fix: str = ""


def check_python() -> Check:
    v = sys.version_info
    ok = v >= (3, 10)
    return Check("Python", "ok" if ok else "fail", f"{platform.python_version()} ({sys.executable})",
                 "" if ok else "Install Python 3.10+")


def check_packages() -> Check:
    missing, found = [], []
    for name in REQUIRED:
        try:
            found.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        return Check("Python packages", "fail", "missing: " + ", ".join(missing),
                     "pip install --upgrade git+https://github.com/deivycom21-cell/ghost-twin.git")
    return Check("Python packages", "ok", ", ".join(found))


def check_chromium(launch: bool = True) -> Check:
    fix = f"{Path(sys.executable).name} -m playwright install chromium"
    if sys.platform.startswith("linux"):
        fix += "  (+ `sudo <python> -m playwright install-deps chromium` for system libs)"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return Check("Chromium", "fail", "playwright not installed", fix)
    try:
        with sync_playwright() as p:
            if not launch:
                path = p.chromium.executable_path
                return Check("Chromium", "ok" if Path(path).exists() else "fail", path,
                             "" if Path(path).exists() else fix)
            browser = p.chromium.launch(headless=True)
            version = browser.version
            browser.close()
            return Check("Chromium", "ok", f"headless launch OK (v{version})")
    except Exception as exc:
        return Check("Chromium", "fail", str(exc).splitlines()[0][:160], fix)


def memory_gb() -> tuple[float | None, float | None]:
    """(total, available) RAM in GB, best effort without psutil."""
    with contextlib.suppress(Exception):
        if sys.platform == "win32":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return st.ullTotalPhys / 1024**3, st.ullAvailPhys / 1024**3
        if Path("/proc/meminfo").exists():
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0]) * 1024
            return info["MemTotal"] / 1024**3, info.get("MemAvailable", 0) / 1024**3
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            return int(out.stdout.strip()) / 1024**3, None
    return None, None


def recommended_browsers(available_gb: float | None) -> int:
    if available_gb is None:
        return 2
    if available_gb < 2:
        return 1
    if available_gb < 4:
        return 2
    if available_gb < 8:
        return 3
    return 4


def check_memory() -> Check:
    total, avail = memory_gb()
    if total is None:
        return Check("Memory", "warn", "could not read RAM", "use --max-browsers 2")
    rec = recommended_browsers(avail)
    detail = f"{total:.1f} GB total" + (f", {avail:.1f} GB free" if avail is not None else "")
    status = "warn" if avail is not None and avail < 1.5 else "ok"
    return Check("Memory", status, f"{detail} → recommended --max-browsers {rec}",
                 "close other apps or use --max-browsers 1 --lite" if status == "warn" else "")


def check_data_dir(data_dir: str | Path) -> Check:
    p = Path(data_dir)
    try:
        p.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=p, delete=True):
            pass
        return Check("Data dir", "ok", str(p.resolve()))
    except OSError as exc:
        return Check("Data dir", "fail", f"{p}: {exc}", "use --data-dir <writable path>")


def check_port(port: int = 8585, host: str = "127.0.0.1") -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return Check("Dashboard port", "warn", f"{host}:{port} is busy",
                         f"ghost-twin dashboard --port {port + 1}")
    return Check("Dashboard port", "ok", f"{host}:{port} free")


def check_ai() -> list[Check]:
    import httpx

    out = []
    claude = shutil.which("claude")
    out.append(Check("AI: claude -p", "ok" if claude else "warn",
                     claude or "not found (optional)",
                     "" if claude else "npm i -g @anthropic-ai/claude-code  (optional)"))
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    if not host.startswith("http"):
        host = "http://" + host
    try:
        r = httpx.get(host.rstrip("/") + "/api/tags", timeout=0.8, trust_env=False)
        models = [m.get("name") for m in r.json().get("models", [])][:4]
        out.append(Check("AI: ollama", "ok", f"{host} ({', '.join(models) or 'no models'})"))
    except Exception:
        out.append(Check("AI: ollama", "warn", f"not reachable at {host} (optional)",
                         "https://ollama.com  (optional)"))
    keys = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if os.environ.get(k)]
    out.append(Check("AI: BYOK keys", "ok" if keys else "warn",
                     ("set: " + ", ".join(keys)) if keys else "none set (optional)"))
    return out


def run_checks(data_dir: str | Path = ".ghost-twin", launch: bool = True,
               port: int = 8585, ai: bool = True) -> list[Check]:
    checks = [check_python(), check_packages(), check_chromium(launch), check_memory(),
              check_data_dir(data_dir), check_port(port)]
    if ai:
        checks += check_ai()
    return checks
