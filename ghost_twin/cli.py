"""ghost-twin command line interface."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ghost_twin import __version__
from ghost_twin.config import DEFAULT_PERSONAS, RunConfig, parse_duration
from ghost_twin.models import Severity

for _stream in (sys.stdout, sys.stderr):  # never crash on emoji in legacy consoles / pipes
    with contextlib.suppress(Exception):
        if _stream.isatty():
            _stream.reconfigure(errors="replace")
        else:
            _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    name="ghost-twin",
    help="👻 Autonomous users, chaos and adversarial testing for your local web apps and APIs.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console(highlight=False)
SEV_STYLE = {"critical": "bold red", "high": "dark_orange", "medium": "yellow", "low": "cyan"}
PERSONA_EMOJI = {"normal_user": "🙂", "chaos_gremlin": "😈", "security_prober": "🕵️",
                 "api_fuzzer": "🔌", "health_check": "❤️"}
FAIL_LEVELS = ["none", "low", "medium", "high", "critical"]


def _version(value: bool) -> None:
    if value:
        console.print(f"ghost-twin {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(False, "--version", "-V", callback=_version, is_eager=True,
                                 help="Show version and exit."),
) -> None:
    """👻 ghost-twin — find bugs before production."""


def _listener(type_: str, data: dict) -> None:
    emoji = PERSONA_EMOJI.get(data.get("persona", ""), "👻")
    if type_ == "finding":
        sev = data.get("severity", "low")
        console.print(f"  {emoji} [{SEV_STYLE.get(sev, '')}]{sev.upper():8}[/] {data.get('title')}")
    elif type_ == "session_start":
        console.print(f"[dim]  {emoji} {data['persona']} #{data['session_id']} started[/]")
    elif type_ == "session_end":
        console.print(f"[dim]  {emoji} {data['persona']} #{data['session_id']} finished: "
                      f"{data['status']} · {data['steps']} steps · {data['findings']} findings[/]")


def _summary_table(bugs) -> Table:
    table = Table(title="Unique bugs", show_lines=False, header_style="bold")
    table.add_column("#", style="dim")
    table.add_column("Severity")
    table.add_column("Type", style="dim")
    table.add_column("Bug")
    table.add_column("Hits", justify="right")
    for i, b in enumerate(bugs, 1):
        sev = b.severity.value
        table.add_row(f"BUG-{i:03d}", f"[{SEV_STYLE[sev]}]{sev}[/]", b.first["kind"],
                      b.first["title"], str(len(b.occurrences)))
    return table


@app.command()
def attack(
    target: str = typer.Argument(..., help="URL of your local app, e.g. http://localhost:3000"),
    users: int = typer.Option(3, "--users", "-u", help="Number of ghost agents."),
    duration: str = typer.Option("60s", "--duration", "-d", help="Total time budget: 30s, 2m, 1h."),
    max_steps: int | None = typer.Option(None, "--max-steps",
                                            help="Max actions per agent (deterministic runs)."),
    max_browsers: int = typer.Option(2, "--max-browsers", "-b",
                                     help="Concurrent browser contexts (RAM limit)."),
    personas: str = typer.Option(",".join(DEFAULT_PERSONAS), "--personas", "-p",
                                 help="Comma-separated personas to use."),
    seed: int = typer.Option(1337, "--seed", help="Random seed (same seed = same plan)."),
    video: bool = typer.Option(True, "--video/--no-video", help="Keep .webm of failing sessions."),
    screenshots: bool = typer.Option(True, "--screenshots/--no-screenshots"),
    headed: bool = typer.Option(False, "--headed", help="Show the browser windows."),
    slow_ms: int = typer.Option(2000, "--slow-ms", help="Slow response threshold (ms)."),
    lite: bool = typer.Option(False, "--lite", help="Block images/fonts/media to save RAM."),
    api: bool = typer.Option(True, "--api/--no-api", help="Fuzz the API if an OpenAPI spec exists."),
    openapi: str | None = typer.Option(None, "--openapi", help="Explicit OpenAPI spec URL."),
    allow_remote: bool = typer.Option(False, "--allow-remote",
                                      help="Allow non-local targets you are authorised to test."),
    aggressive: bool = typer.Option(False, "--aggressive",
                                    help="Add tautology SQLi payloads (disposable DBs only!)."),
    allow_destructive: bool = typer.Option(False, "--allow-destructive",
                                           help="Allow clicking delete/logout/pay-like controls."),
    data_dir: Path = typer.Option(Path(".ghost-twin"), "--data-dir", help="Where data is stored."),
    ai: str = typer.Option("none", "--ai",
                           help="Optional AI: none | claude | ollama | anthropic | openai."),
    report: Path = typer.Option(Path("BUG_REPORT.md"), "--report", "-o", help="Report output path."),
    lang: str = typer.Option("en", "--lang", help="Report language: en | es."),
    dashboard: bool = typer.Option(False, "--dashboard", help="Serve the live dashboard too."),
    port: int = typer.Option(8585, "--port", help="Dashboard port."),
    fail_on: str = typer.Option("none", "--fail-on",
                                help="Exit 1 if a bug >= this severity exists (CI): "
                                     "none|low|medium|high|critical."),
) -> None:
    """🚀 Unleash ghost users against TARGET and write BUG_REPORT.md."""
    from ghost_twin.ai import AIError, get_provider
    from ghost_twin.engine.orchestrator import Orchestrator, TargetUnreachable
    from ghost_twin.recorder.store import Store
    from ghost_twin.reporter import group_findings, write_report
    from ghost_twin.safety import ScopeError

    if fail_on not in FAIL_LEVELS:
        console.print(f"[red]--fail-on must be one of {', '.join(FAIL_LEVELS)}[/]")
        raise typer.Exit(2)
    try:
        cfg = RunConfig(
            target=target, users=users, duration=parse_duration(duration), max_steps=max_steps,
            max_browsers=max_browsers, personas=[p.strip() for p in personas.split(",") if p.strip()],
            seed=seed, headless=not headed, video="on-failure" if video else "off",
            screenshots=screenshots, slow_ms=slow_ms, lite=lite, allow_remote=allow_remote,
            aggressive=aggressive, allow_destructive=allow_destructive, data_dir=data_dir, ai=ai,
            api=api, openapi=openapi,
        ).validate()
        from ghost_twin.safety import ScopeGuard

        ScopeGuard(cfg.target, cfg.allow_remote).check_target()
    except (ValueError, ScopeError) as exc:
        console.print(f"[red]✗ {exc}[/]")
        raise typer.Exit(2) from exc

    store = Store.in_dir(cfg.data_dir)
    provider = None
    if ai != "none":
        try:
            provider = get_provider(ai, store)
            console.print(f"[dim]AI backend: {provider.name} {provider.model}[/]")
        except AIError as exc:
            console.print(f"[yellow]! AI disabled ({exc}); continuing with heuristics[/]")

    dash = None
    if dashboard:
        from ghost_twin.web.server import DashboardThread

        try:
            dash = DashboardThread(cfg.data_dir, port=port).start()
            console.print(f"[bold]📺 Live dashboard:[/] {dash.url}")
        except Exception as exc:
            console.print(f"[yellow]! dashboard not started: {exc}[/]")

    console.print(
        f"\n[bold]👻 ghost-twin[/] → [cyan]{cfg.target}[/]  ·  {cfg.users} agents "
        f"({', '.join(cfg.personas)})  ·  {duration}  ·  max {cfg.max_browsers} browsers  ·  "
        f"seed {cfg.seed}\n")
    orch = Orchestrator(cfg, store, _listener, provider)
    interrupted = False
    try:
        result = asyncio.run(orch.run())
        status = result.status
    except TargetUnreachable as exc:
        console.print(f"[red]✗ {exc}[/]")
        raise typer.Exit(2) from exc
    except KeyboardInterrupt:
        interrupted, status = True, "interrupted"
        console.print("\n[yellow]Interrupted — writing report for what was found so far…[/]")

    if orch.run_id is None:
        raise typer.Exit(130 if interrupted else 1)
    out = write_report(store, orch.run_id, report, data_dir=cfg.data_dir, lang=lang, ai=provider)
    bugs = group_findings(store.findings(orch.run_id))
    console.print()
    if bugs:
        console.print(_summary_table(bugs))
    else:
        console.print("[green]✓ No bugs found.[/]")
    console.print(f"\n[bold]Run #{orch.run_id}[/] {status} · report: [bold]{out}[/]")
    if bugs:
        console.print(f'[dim]Next: claude "Fix the bugs described in {out.name}"[/]')

    if dash is not None and not interrupted:
        console.print(f"[dim]Dashboard still at {dash.url} — Ctrl+C to exit.[/]")
        with contextlib.suppress(KeyboardInterrupt):
            while True:
                time.sleep(1)
        dash.stop()

    if fail_on != "none" and bugs:
        threshold = Severity.parse(fail_on).rank
        if any(b.severity.rank >= threshold for b in bugs):
            raise typer.Exit(1)
    if interrupted:
        raise typer.Exit(130)


@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8585, "--port"),
    data_dir: Path = typer.Option(Path(".ghost-twin"), "--data-dir"),
    open_browser: bool = typer.Option(False, "--open", help="Open it in your browser."),
) -> None:
    """📺 Serve the live dark dashboard (default http://127.0.0.1:8585)."""
    from ghost_twin.web.server import serve

    url = f"http://{host}:{port}"
    console.print(f"[bold]📺 ghost-twin dashboard[/] → {url}  [dim](data: {data_dir})[/]")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        console.print("[yellow]! Exposing the dashboard beyond localhost shares findings and "
                      "payloads with your network.[/]")
    if open_browser:
        webbrowser.open(url)
    serve(data_dir, host, port)


@app.command()
def report(
    run_id: int | None = typer.Argument(None, help="Run id (default: latest)."),
    out: Path = typer.Option(Path("BUG_REPORT.md"), "--out", "-o"),
    data_dir: Path = typer.Option(Path(".ghost-twin"), "--data-dir"),
    lang: str = typer.Option("en", "--lang"),
    ai: str = typer.Option("none", "--ai", help="Add an AI analysis section."),
) -> None:
    """📝 (Re)generate BUG_REPORT.md for a run."""
    from ghost_twin.ai import AIError, get_provider
    from ghost_twin.recorder.store import Store
    from ghost_twin.reporter import write_report

    store = Store.in_dir(data_dir)
    rid = run_id or store.latest_run_id()
    if rid is None or store.get_run(rid) is None:
        console.print("[red]✗ no such run. Use `ghost-twin runs` to list them.[/]")
        raise typer.Exit(1)
    provider = None
    if ai != "none":
        try:
            provider = get_provider(ai, store)
        except AIError as exc:
            console.print(f"[yellow]! AI disabled: {exc}[/]")
    path = write_report(store, rid, out, data_dir=data_dir, lang=lang, ai=provider)
    console.print(f"✓ report for run #{rid} → [bold]{path}[/]")


@app.command()
def runs(data_dir: Path = typer.Option(Path(".ghost-twin"), "--data-dir"),
         limit: int = typer.Option(20, "--limit")) -> None:
    """📚 List previous runs."""
    from datetime import datetime

    from ghost_twin.recorder.store import Store

    store = Store.in_dir(data_dir)
    rows = store.list_runs(limit)
    if not rows:
        console.print("No runs yet.")
        return
    table = Table(header_style="bold")
    for col in ("Run", "Target", "Started", "Status", "Agents", "Findings"):
        table.add_column(col)
    for r in rows:
        started = datetime.fromtimestamp(r["started_at"]).strftime("%Y-%m-%d %H:%M")
        table.add_row(f"#{r['id']}", r["target"], started, r["status"], str(r["sessions"]),
                      str(r["findings"]))
    console.print(table)


@app.command()
def doctor(
    data_dir: Path = typer.Option(Path(".ghost-twin"), "--data-dir"),
    launch: bool = typer.Option(True, "--launch/--no-launch",
                                help="Actually launch Chromium to verify it works."),
    port: int = typer.Option(8585, "--port"),
) -> None:
    """🩺 Check Python, Playwright/Chromium, RAM, ports and optional AI backends."""
    from ghost_twin.doctor import run_checks

    checks = run_checks(data_dir, launch=launch, port=port)
    icon = {"ok": "[green]✓[/]", "warn": "[yellow]![/]", "fail": "[red]✗[/]"}
    table = Table(header_style="bold", title="ghost-twin doctor")
    table.add_column("")
    table.add_column("Check")
    table.add_column("Detail")
    table.add_column("Fix", style="dim")
    for c in checks:
        table.add_row(icon[c.status], c.name, c.detail, c.fix)
    console.print(table)
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(1)
    console.print("[green]Ready to haunt your app. 👻[/]")


@app.command()
def demo(host: str = typer.Option("127.0.0.1", "--host"),
         port: int = typer.Option(3000, "--port")) -> None:
    """🧪 Start a deliberately buggy demo app to try ghost-twin on."""
    from ghost_twin.demo import serve

    console.print(f"Try: [bold]ghost-twin attack http://{host}:{port} --duration 45s --dashboard[/]")
    serve(host, port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
