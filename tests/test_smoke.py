import importlib
import re

from typer.testing import CliRunner

import ghost_twin
from ghost_twin.cli import app

MODULES = [
    "ghost_twin.cli", "ghost_twin.config", "ghost_twin.safety", "ghost_twin.models",
    "ghost_twin.doctor", "ghost_twin.engine.orchestrator", "ghost_twin.engine.agent",
    "ghost_twin.engine.browser", "ghost_twin.engine.health", "ghost_twin.personas.registry",
    "ghost_twin.personas.snapshot", "ghost_twin.recorder.collector", "ghost_twin.recorder.store",
    "ghost_twin.reporter.markdown", "ghost_twin.ai.providers", "ghost_twin.ai.tasks",
    "ghost_twin.api.openapi_fuzzer", "ghost_twin.web.server", "ghost_twin.demo.buggy_app",
]


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", ghost_twin.__version__)


def test_all_modules_import():
    for name in MODULES:
        importlib.import_module(name)


def test_cli_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert ghost_twin.__version__ in result.output


def test_cli_help_lists_commands():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("attack", "dashboard", "doctor", "report", "runs", "demo"):
        assert cmd in result.output
