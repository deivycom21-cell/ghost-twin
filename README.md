<div align="center">

# 👻 ghost-twin

**Usuarios fantasma, caos y pruebas adversarias para tus apps web y APIs locales.**
**Ghost users, chaos and adversarial testing for your local web apps and APIs.**

Encuentra bugs *antes* de producción · Find bugs *before* production

[Español](#-español) · [English](#-english)

![python](https://img.shields.io/badge/python-3.10%2B-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![ram](https://img.shields.io/badge/RAM-8%20GB%20friendly-purple) ![ai](https://img.shields.io/badge/AI-optional-lightgrey)

</div>

```text
$ ghost-twin attack http://localhost:3000 --users 4 --duration 60s --dashboard

👻 ghost-twin → http://localhost:3000 · 4 agents · 60s · max 2 browsers · seed 1337
  🙂 HIGH     Server error 500 on GET /api/cart
  😈 MEDIUM   Duplicate submission accepted on POST /api/signup
  🕵️ CRITICAL Database error leaked on GET /search via `q` (possible SQL injection)
  🕵️ HIGH     Reflected XSS on GET /search via `q`
  🔌 HIGH     API 500 on GET /api/items/{item_id} (item_id: non-numeric)
  …
Run #1 finished · report: BUG_REPORT.md
Next: claude "Fix the bugs described in BUG_REPORT.md"
```

---

## 🇪🇸 Español

`ghost-twin` lanza **agentes fantasma** (navegadores headless reales) contra tu app en
`localhost`: un usuario normal que completa flujos, un *gremlin* que hace doble clic y pulsa F5
en mitad de un envío, y un *prober* de seguridad que prueba inyecciones de forma segura. Graba la
evidencia de cada fallo (consola, red, captura y video) y genera un **`BUG_REPORT.md` listo para
pegar en Claude Code o Cursor**.

- 🪶 **Ligero:** un solo Chromium headless, agentes como contextos aislados, límite `--max-browsers`. Pensado para 8 GB de RAM.
- 🏠 **Local-first:** todo en `./.ghost-twin/` (SQLite + artefactos). Sin cuentas, sin nube, sin telemetría.
- 🎲 **Determinista:** misma `--seed` ⇒ mismo plan de acciones. Reproducible.
- 🤖 **IA opcional:** funciona 100 % sin API key. Si quieres, conecta `claude -p`, Ollama local o tu propia API key (BYOK).
- 🛡️ **Seguro por defecto:** solo ataca hosts locales/privados, payloads de detección (no destructivos) y evita botones tipo "Eliminar"/"Cerrar sesión"/"Pagar".

### Instalación

Requisitos: Python 3.10+ y git.

```bash
pip install git+https://github.com/deivycom21-cell/ghost-twin.git
python -m playwright install chromium        # una sola vez (~150 MB)
ghost-twin doctor                            # verifica todo
```

Instalación rápida con script (crea el entorno, descarga Chromium y deja `ghost-twin` en el PATH):

```bash
# Linux / macOS / Kali (respeta PEP 668: usa pipx o un virtualenv privado)
curl -fsSL https://raw.githubusercontent.com/deivycom21-cell/ghost-twin/main/scripts/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/deivycom21-cell/ghost-twin/main/scripts/install.ps1 | iex
```

> **Kali / Debian / Ubuntu:** si `pip install` falla con *externally-managed-environment*, usa
> `pipx install git+https://github.com/deivycom21-cell/ghost-twin.git` o el script de arriba.
> Si Chromium no arranca: `sudo $(which python3) -m playwright install-deps chromium`.

### Pruébalo en 30 segundos

```bash
ghost-twin demo                     # terminal 1: app de demo con bugs plantados en :3000
ghost-twin attack http://localhost:3000 --duration 45s --dashboard   # terminal 2
```

Abre <http://127.0.0.1:8585> para ver a los agentes en vivo.

### Comandos

| Comando | Qué hace |
|---|---|
| `ghost-twin attack URL [opciones]` | Lanza los agentes y escribe `BUG_REPORT.md` |
| `ghost-twin dashboard` | Panel oscuro en vivo en `127.0.0.1:8585` |
| `ghost-twin report [RUN_ID]` | Regenera el reporte de una ejecución (`--lang es`, `--ai …`) |
| `ghost-twin runs` | Lista ejecuciones anteriores |
| `ghost-twin doctor` | Revisa Python, Chromium, RAM, puertos e IA opcional |
| `ghost-twin demo` | Arranca una app con bugs para probar |

Opciones útiles de `attack`:

| Opción | Defecto | Descripción |
|---|---|---|
| `--users / -u` | `3` | Número de agentes fantasma |
| `--duration / -d` | `60s` | Presupuesto de tiempo (`30s`, `2m`, `1h`) |
| `--max-browsers / -b` | `2` | Contextos simultáneos (controla la RAM) |
| `--personas / -p` | las 3 | `normal_user,chaos_gremlin,security_prober` |
| `--max-steps` | – | Acciones máximas por agente (ejecuciones deterministas) |
| `--seed` | `1337` | Misma semilla ⇒ mismas decisiones |
| `--dashboard` | off | Sirve el panel en vivo durante el ataque |
| `--lite` | off | Bloquea imágenes/fuentes/media para ahorrar RAM |
| `--no-video` | – | No guardar `.webm` de sesiones fallidas |
| `--slow-ms` | `2000` | Umbral de respuesta lenta |
| `--api / --no-api` | on | Fuzzea la API si encuentra un spec OpenAPI |
| `--openapi URL` | auto | Spec OpenAPI explícito |
| `--ai` | `none` | `claude`, `ollama`, `anthropic`, `openai` |
| `--lang` | `en` | Idioma del reporte: `en` / `es` |
| `--fail-on` | `none` | Código de salida 1 si hay bugs ≥ severidad (CI) |
| `--allow-remote` | off | Permite hosts no locales **que te pertenezcan** |
| `--aggressive` | off | Añade SQLi tipo tautología (solo BD desechables) |
| `--allow-destructive` | off | Permite pulsar eliminar/logout/pagar |

### Los agentes

| Persona | Comportamiento |
|---|---|
| 🙂 `normal_user` | Explora enlaces y completa formularios con datos válidos y realistas (email, teléfono, fechas, rangos `min/max`…). |
| 😈 `chaos_gremlin` | Doble clic en enviar, F5 en mitad del envío, cierra la pestaña de golpe, formularios vacíos/gigantes/unicode/tipos cruzados, red lenta (3G), atrás/recargar. Desactiva la validación del navegador para probar la del servidor. |
| 🕵️ `security_prober` | Prueba SQLi por errores (`'`, `"`), XSS reflejado con marcadores únicos y path traversal en parámetros URL e inputs. |
| 🔌 `api_fuzzer` | Si hay `openapi.json`/`swagger.json`, fuzzea cada endpoint: tipos erróneos, campos requeridos ausentes, JSON roto, overflow, payloads. Incluye el `curl` exacto para reproducir. |
| ❤️ `health_check` | Detecta si tu servidor se cae durante el ataque y lista las últimas acciones previas. |

### Qué detecta

| Tipo | Severidad |
|---|---|
| `server_crash` — el servidor deja de responder | critical |
| `sql_error_leak` — error de BD expuesto (posible SQLi) | critical |
| `path_traversal` — lectura de `/etc/passwd` / `win.ini` | critical |
| `reflected_xss` — payload reflejado sin escapar / ejecutado | high |
| `http_5xx` — error del servidor | high |
| `js_exception` — excepción JS no capturada | high |
| `double_submit` — doble envío aceptado (sin idempotencia) | medium |
| `console_error` — `console.error` | medium |
| `http_4xx` / `request_failed` / `slow_response` | low |

### El reporte para tu agente

`BUG_REPORT.md` incluye instrucciones para el agente, tabla resumen y, por cada bug único:
severidad, URL, método, parámetro, **payload exacto**, **pasos para reproducir**, `curl`,
evidencia de consola/red, captura, video y un arreglo sugerido. Luego:

```bash
claude "Arregla los bugs descritos en BUG_REPORT.md, uno por uno, con un test de regresión cada uno"
```

### IA opcional (BYOK / local)

Sin IA todo funciona con heurísticas. Con IA, `normal_user` genera datos más realistas y el
reporte añade un análisis de causas raíz. Las respuestas se cachean en SQLite (no pagas dos veces).
**La IA nunca decide payloads de seguridad.**

| `--ai` | Configuración |
|---|---|
| `claude` | Usa tu Claude Code local (`claude -p`). Modelo opcional: `GHOST_TWIN_AI_MODEL` |
| `ollama` | `OLLAMA_HOST` (defecto `http://127.0.0.1:11434`), `OLLAMA_MODEL` (defecto `llama3.2`) |
| `anthropic` | `ANTHROPIC_API_KEY`, `GHOST_TWIN_AI_MODEL` |
| `openai` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` (cualquier API compatible: LM Studio, OpenRouter, vLLM…), `OPENAI_MODEL` |

### Uso en CI

```yaml
- run: pip install git+https://github.com/deivycom21-cell/ghost-twin.git
- run: python -m playwright install --with-deps chromium
- run: npm start &   # tu app
- run: ghost-twin attack http://localhost:3000 --duration 90s --fail-on high --no-video
- uses: actions/upload-artifact@v4
  if: always()
  with: { name: ghost-twin, path: "BUG_REPORT.md\n.ghost-twin/runs" }
```

### Consejos para 8 GB de RAM

- `--max-browsers 2` (defecto) ≈ 0.5 GB. `ghost-twin doctor` te recomienda un valor según tu RAM libre.
- `--users` puede ser mayor que `--max-browsers`: los agentes esperan su turno.
- `--lite` bloquea imágenes y fuentes; `--no-video` ahorra CPU.

### ⚠️ Uso responsable

Usa ghost-twin **solo contra aplicaciones que te pertenecen o que estás autorizado a probar**, y
preferiblemente contra una base de datos de desarrollo: los agentes envían formularios reales.
No es una herramienta de pentest contra sistemas ajenos ni de carga/DoS.

---

## 🇬🇧 English

`ghost-twin` unleashes **ghost agents** (real headless browsers) against your app on
`localhost`: a normal user completing flows, a *gremlin* that double-clicks and hits F5 mid-submit,
and a security *prober* that safely tries injections. It records evidence for every failure
(console, network, screenshot and video) and writes a **`BUG_REPORT.md` ready to paste into
Claude Code or Cursor**.

- 🪶 **Lightweight:** one headless Chromium, agents as isolated contexts, `--max-browsers` cap. Built for 8 GB RAM.
- 🏠 **Local-first:** everything lives in `./.ghost-twin/` (SQLite + artifacts). No accounts, no cloud, no telemetry.
- 🎲 **Deterministic:** same `--seed` ⇒ same action plan. Reproducible.
- 🤖 **Optional AI:** works 100% without an API key. Optionally plug in `claude -p`, local Ollama or your own key (BYOK).
- 🛡️ **Safe by default:** local/private hosts only, detection-only payloads, avoids "Delete"/"Log out"/"Pay"-like controls.

### Install

Requires Python 3.10+ and git.

```bash
pip install git+https://github.com/deivycom21-cell/ghost-twin.git
python -m playwright install chromium        # once (~150 MB)
ghost-twin doctor                            # check everything
```

One-liners (set up the environment, download Chromium, put `ghost-twin` on PATH):

```bash
# Linux / macOS / Kali (PEP 668 aware: uses pipx or a private virtualenv)
curl -fsSL https://raw.githubusercontent.com/deivycom21-cell/ghost-twin/main/scripts/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/deivycom21-cell/ghost-twin/main/scripts/install.ps1 | iex
```

### Try it in 30 seconds

```bash
ghost-twin demo                     # terminal 1: demo app with planted bugs on :3000
ghost-twin attack http://localhost:3000 --duration 45s --dashboard   # terminal 2
```

Open <http://127.0.0.1:8585> to watch the agents live.

### Commands

| Command | What it does |
|---|---|
| `ghost-twin attack URL [options]` | Runs the agents and writes `BUG_REPORT.md` |
| `ghost-twin dashboard` | Live dark dashboard on `127.0.0.1:8585` |
| `ghost-twin report [RUN_ID]` | Regenerates a run's report (`--lang es`, `--ai …`) |
| `ghost-twin runs` | Lists previous runs |
| `ghost-twin doctor` | Checks Python, Chromium, RAM, ports and optional AI |
| `ghost-twin demo` | Starts a buggy app to practice on |

See the Spanish section above for the full option table — flags are identical
(`--users`, `--duration`, `--max-browsers`, `--personas`, `--max-steps`, `--seed`, `--dashboard`,
`--lite`, `--no-video`, `--slow-ms`, `--api/--no-api`, `--openapi`, `--ai`, `--lang`, `--fail-on`,
`--allow-remote`, `--aggressive`, `--allow-destructive`). Run `ghost-twin attack --help`.

### Agents

| Persona | Behaviour |
|---|---|
| 🙂 `normal_user` | Explores links and completes forms with valid, realistic data. |
| 😈 `chaos_gremlin` | Double-submits, F5 mid-submit, slams tabs shut, empty/huge/unicode/type-confused forms, slow 3G, back/reload. Disables browser validation to test the server's. |
| 🕵️ `security_prober` | Error-based SQLi (`'`, `"`), reflected XSS with unique markers and path traversal on URL params and inputs. |
| 🔌 `api_fuzzer` | If an `openapi.json`/`swagger.json` exists, fuzzes every endpoint and gives you the exact `curl`. |
| ❤️ `health_check` | Detects when your server dies mid-attack and lists the actions that preceded it. |

### The report

`BUG_REPORT.md` contains instructions for the coding agent, a summary table and, per unique bug:
severity, URL, method, parameter, **exact payload**, **steps to reproduce**, `curl`, console/network
evidence, screenshot, video and a suggested fix. Then:

```bash
claude "Fix the bugs described in BUG_REPORT.md one by one, adding a regression test for each"
```

### Optional AI (BYOK / local)

| `--ai` | Configuration |
|---|---|
| `claude` | Your local Claude Code (`claude -p`). Optional model: `GHOST_TWIN_AI_MODEL` |
| `ollama` | `OLLAMA_HOST` (default `http://127.0.0.1:11434`), `OLLAMA_MODEL` (default `llama3.2`) |
| `anthropic` | `ANTHROPIC_API_KEY`, `GHOST_TWIN_AI_MODEL` |
| `openai` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` (any compatible API), `OPENAI_MODEL` |

AI only makes form data more realistic and adds a root-cause analysis to the report; responses
are cached in SQLite. **AI never chooses security payloads.**

### ⚠️ Responsible use

Only point ghost-twin at **apps you own or are authorised to test**, ideally backed by a
development database — agents submit real forms. It is not a pentest tool for third-party systems
and not a load/DoS tool.

---

## 🛠️ Development

```bash
git clone https://github.com/deivycom21-cell/ghost-twin.git && cd ghost-twin
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium
pytest -m "not e2e"      # fast unit tests, no browser
pytest -m e2e            # real Chromium vs. the bundled buggy app (offline, no tokens)
ruff check .
```

Architecture and phases: see [`PLAN.md`](PLAN.md).

```text
ghost_twin/
├── cli.py            Typer CLI
├── engine/           orchestrator, agent runner, browser, health-check
├── personas/         normal_user, chaos_gremlin, security_prober (+ datagen, payloads)
├── recorder/         page listeners, pure detectors, artifacts, SQLite store
├── reporter/         dedupe + BUG_REPORT.md
├── api/              OpenAPI discovery + fuzzing
├── ai/               optional providers (claude -p, Ollama, Anthropic, OpenAI-compatible)
├── web/              FastAPI + WebSocket dashboard (vanilla JS)
└── demo/             deliberately buggy app used by tests and `ghost-twin demo`
```

## License

[MIT](LICENSE)
