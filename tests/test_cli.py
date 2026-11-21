from typer.testing import CliRunner

from ghost_twin import doctor as doctor_mod
from ghost_twin.cli import app
from ghost_twin.recorder.store import Store
from tests.test_reporter import populate

runner = CliRunner()


def test_attack_refuses_remote_targets(tmp_path):
    r = runner.invoke(app, ["attack", "https://example.com", "--data-dir", str(tmp_path)])
    assert r.exit_code == 2 and "--allow-remote" in r.output


def test_attack_validates_options(tmp_path):
    for args in (["--duration", "soon"], ["--personas", "hacker"], ["--fail-on", "meh"],
                 ["--users", "0"]):
        r = runner.invoke(app, ["attack", "http://localhost:1", "--data-dir", str(tmp_path), *args])
        assert r.exit_code == 2, args


def test_attack_unreachable_target(tmp_path):
    r = runner.invoke(app, ["attack", "http://127.0.0.1:9", "--data-dir", str(tmp_path),
                            "--duration", "5s"])
    assert r.exit_code == 2 and "not reachable" in r.output


def test_report_and_runs(tmp_path):
    store = Store.in_dir(tmp_path)
    populate(store)
    out = tmp_path / "R.md"
    r = runner.invoke(app, ["report", "--data-dir", str(tmp_path), "--out", str(out), "--lang", "es"])
    assert r.exit_code == 0, r.output
    assert "Pasos para reproducir" in out.read_text(encoding="utf-8")
    r = runner.invoke(app, ["runs", "--data-dir", str(tmp_path)])
    assert r.exit_code == 0 and "target_down" in r.output


def test_report_missing_run(tmp_path):
    r = runner.invoke(app, ["report", "42", "--data-dir", str(tmp_path)])
    assert r.exit_code == 1


def test_doctor(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor_mod, "run_checks", lambda *a, **k: [
        doctor_mod.Check("Python", "ok", "3.12"), doctor_mod.Check("AI: ollama", "warn", "off")])
    r = runner.invoke(app, ["doctor", "--data-dir", str(tmp_path)])
    assert r.exit_code == 0 and "Ready" in r.output
    monkeypatch.setattr(doctor_mod, "run_checks", lambda *a, **k: [
        doctor_mod.Check("Chromium", "fail", "missing", "playwright install chromium")])
    r = runner.invoke(app, ["doctor", "--data-dir", str(tmp_path)])
    assert r.exit_code == 1 and "playwright install chromium" in r.output


def test_doctor_real_checks_without_browser(tmp_path):
    checks = {c.name: c for c in doctor_mod.run_checks(tmp_path, launch=False, port=0, ai=False)}
    assert checks["Python"].status == "ok"
    assert checks["Python packages"].status == "ok"
    assert checks["Data dir"].status == "ok"


def test_recommended_browsers():
    assert [doctor_mod.recommended_browsers(x) for x in (None, 1, 3, 6, 12)] == [2, 1, 2, 3, 4]
