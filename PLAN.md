# ghost-twin — Plan de implementación

> Simulador autónomo de usuarios reales, caos y pruebas adversarias para apps web y APIs
> locales. Local-first, 8 GB RAM friendly, IA opcional, cero costos por defecto.

## 0. Principios

| Principio | Decisión concreta |
|---|---|
| **Ligero** | Un solo proceso Chromium headless; cada agente es un `BrowserContext` (≈40-80 MB) limitado por un `asyncio.Semaphore` (`--max-browsers`, por defecto 2). Opción `--lite` bloquea fuentes/imágenes/media. |
| **Local-first** | Todo se guarda en `./.ghost-twin/` (SQLite WAL + artefactos). Sin telemetría. |
| **Determinista** | Toda decisión aleatoria sale de `random.Random(seed + agent_index)`. Misma semilla + misma app ⇒ mismo plan de acciones. Los tests usan `--max-steps` en vez de duración. |
| **IA opcional** | `--ai none` (defecto) usa heurísticas. `claude` (`claude -p`), `ollama`, `anthropic`, `openai` (cualquier API OpenAI-compatible). La IA **nunca** decide payloads de seguridad: solo genera datos de formulario realistas y redacta el reporte. Respuestas cacheadas en SQLite (hash del prompt). |
| **Seguro por defecto** | Solo ataca `localhost`, `127.0.0.0/8`, `::1`, redes privadas y `*.local/*.test/*.localhost`; otro host requiere `--allow-remote`. Payloads de detección (provocan errores, no destruyen): sin tautologías `OR 1=1` ni `DROP` salvo `--aggressive`. Botones con texto destructivo (delete/eliminar/borrar/logout/pagar…) se evitan salvo `--allow-destructive`. Nunca sale del mismo origen. |
| **Evidencia útil** | Captura + video **solo** de sesiones con fallos; el resto se borra al cerrar el contexto. |

## 1. Arquitectura

```
ghost_twin/
├── __init__.py            versión
├── __main__.py            python -m ghost_twin
├── cli.py                 Typer: attack | dashboard | report | runs | doctor | demo
├── config.py              RunConfig, parse_duration("60s"|"2m"|"1h")
├── safety.py              ScopeGuard (localhost/privado), filtro de acciones destructivas
├── models.py              dataclasses: Action, Finding, Severity, PageSnapshot, FormInfo…
├── doctor.py              chequeos de entorno
├── engine/
│   ├── browser.py         lanzar Chromium con flags de baja RAM, contextos con/sin video
│   ├── orchestrator.py    reparte agentes, semáforo, health-check del target, fin por tiempo/pasos
│   └── health.py          ping periódico al target → hallazgo "server caído"
├── personas/
│   ├── base.py            Persona (ABC): loop observe → decide → act, registro de pasos
│   ├── snapshot.py        extrae links/forms/inputs/botones de la página (JS inyectado)
│   ├── datagen.py         datos válidos por type/name/placeholder (email, tel, fecha…)
│   ├── payloads.py        catálogo SQLi/XSS/Traversal + firmas de error de BD
│   ├── normal_user.py
│   ├── chaos_gremlin.py
│   ├── security_prober.py
│   └── registry.py        nombre → clase, reparto de mezcla (--personas)
├── recorder/
│   ├── store.py           SQLite: runs, sessions, steps, events, findings, ai_cache
│   ├── collector.py       listeners de página: console, pageerror, response, requestfailed, timing
│   ├── detectors.py       funciones puras: evento/respuesta → Finding
│   └── artifacts.py       screenshot/video: conservar solo si la sesión falló
├── reporter/
│   ├── dedupe.py          fingerprint (tipo + método + ruta normalizada + firma)
│   ├── markdown.py        BUG_REPORT.md optimizado para Claude Code / Cursor
│   └── hints.py           sugerencia de arreglo determinista por tipo de bug
├── ai/
│   ├── base.py            Protocol AIProvider + caché
│   ├── providers.py       none | claude_cli | ollama | anthropic | openai_compat
│   └── prompts.py         prompts para datos de persona y redacción del reporte
├── api/
│   └── openapi_fuzzer.py  modo API: descubre /openapi.json y fuzzea endpoints con httpx
├── demo/
│   └── buggy_app.py       app web con bugs plantados (tests + `ghost-twin demo`)
└── web/
    ├── server.py          FastAPI: /api/runs, /api/runs/{id}, /artifacts, /ws (tail SQLite)
    └── static/            index.html, app.js, style.css (vanilla, tema oscuro)
```

**Flujo de datos:** personas → `collector` → `detectors` → `store` (SQLite, tabla `events` con id
autoincremental). El dashboard (mismo proceso o `ghost-twin dashboard` aparte) hace *tail* de
`events WHERE id > last_id` y lo empuja por WebSocket ⇒ en vivo sin IPC extra.

### Modelo de hallazgos

| Tipo | Detector | Severidad |
|---|---|---|
| `server_crash` | health-check falla tras haber respondido | critical |
| `sql_error_leak` | respuesta contiene firma de error SQL tras payload | critical |
| `path_traversal` | respuesta contiene `root:x:0:0` / `[fonts]` / `for 16-bit app support` | critical |
| `reflected_xss` | marcador único sin escapar en el HTML / ejecuta `alert` | high |
| `http_5xx` | respuesta del mismo origen ≥ 500 | high |
| `js_exception` | `pageerror` | high |
| `double_submit` | 2+ POST idénticos con 2xx en < 1 s tras doble clic | medium |
| `console_error` | `console.error` | medium |
| `http_4xx` | recurso del mismo origen 4xx (no navegaciones con payload) | low |
| `request_failed` | `requestfailed` (no abortado por nosotros) | low |
| `slow_response` | > `--slow-ms` (defecto 2000) | low |

## 2. Fases atómicas

Cada fase termina con tests verdes. **Todos los tests son offline y sin tokens**: la app objetivo es
`ghost_twin/demo/buggy_app.py`, un servidor `http.server` de la stdlib levantado en un puerto
aleatorio por un fixture de pytest. Los tests de IA usan proveedores falsos / `subprocess` y
`httpx` mockeados.

- **Unit** (`pytest`): rápidos, sin navegador.
- **E2E** (`@pytest.mark.e2e`): usan Chromium real contra la buggy app; se saltan automáticamente si
  Chromium no está instalado.

### Fase 0 — Andamiaje
- `pyproject.toml` (hatchling, entry point `ghost-twin`), `README.md` bilingüe, `LICENSE` (MIT),
  `.gitignore`, `scripts/install.ps1`, `scripts/install.sh`.
- **Test:** `test_smoke.py` — importa el paquete, `__version__` válido, `ghost-twin --version`.

### Fase 1 — Config y seguridad
- `parse_duration`, `RunConfig` validado, `ScopeGuard`, `is_destructive_label`.
- **Tests:** `"60s"→60`, `"2m"→120`, `"1h30m"→5400`, errores; `localhost`, `127.0.0.1`,
  `192.168.x`, `[::1]`, `app.test` permitidos; `example.com` rechazado salo `allow_remote`;
  "Eliminar cuenta" es destructivo, "Enviar" no.

### Fase 2 — Almacenamiento
- Esquema SQLite (WAL), API `Store` síncrona y thread-safe.
- **Tests:** crear run → sesiones → pasos → eventos → hallazgos; `events_since(id)` devuelve en orden;
  la BD se reabre sin perder datos.

### Fase 3 — App objetivo con bugs plantados (`demo/buggy_app.py`)
| Ruta | Bug plantado |
|---|---|
| `/` | enlaces a todo; `console.error` en el load |
| `/search?q=` | refleja `q` sin escapar (XSS) y da 500 con firma SQL si `q` contiene `'` |
| `/file?name=` | traversal: `../` devuelve un `/etc/passwd` simulado |
| `/contact` (POST) | 500 si el mensaje está vacío o > 5000 chars |
| `/signup` (POST) | crea duplicados en doble submit (sin idempotencia) |
| `/slow` | tarda 2.5 s |
| `/broken-js` | lanza excepción JS |
| `/api/items` + `/openapi.json` | API JSON: 500 con `id` no numérico |
| `/crash` | oculto, no enlazado: tumba el server (para test de health) |
- **Tests:** cada ruta se comporta como se espera vía `urllib` (sin navegador).

### Fase 4 — Detectores (puros)
- Funciones `detect_*` que reciben datos planos y devuelven `Finding | None`.
- **Tests:** tablas de casos: firmas SQL de MySQL/Postgres/SQLite/MSSQL, marcador XSS escapado vs.
  no escapado, passwd/win.ini, umbral lento, 4xx/5xx, mismo origen vs. tercero.

### Fase 5 — Personas (lógica pura)
- `PageSnapshot` + `datagen` + `payloads` + `decide()` de cada persona sobre un snapshot.
- **Tests:** misma semilla ⇒ mismas decisiones; `normal_user` rellena email válido en `type=email`;
  `chaos_gremlin` genera vacío/gigante/unicode; `security_prober` nunca usa tautologías sin
  `aggressive`; ninguna persona elige un botón destructivo; nunca enlaces fuera de origen.

### Fase 6 — Navegador + colector (E2E)
- Lanzamiento con flags de baja memoria, contexto con video opcional, `collector` adjunto.
- **Tests E2E:** visitar `/` registra `console_error`; `/broken-js` registra `js_exception`;
  `/slow` registra `slow_response` con `slow_ms=1000`.

### Fase 7 — Personas en acción (E2E)
- **Tests E2E:** con `max_steps` fijos y semilla fija contra la buggy app:
  `normal_user` → `console_error` + `http_5xx` en `/contact`;
  `chaos_gremlin` → `http_5xx` por formulario vacío/gigante y `double_submit` en `/signup`;
  `security_prober` → `reflected_xss`, `sql_error_leak`, `path_traversal`.

### Fase 8 — Orquestador + artefactos
- `--users`, `--duration`, `--max-steps`, `--max-browsers`, `--video`, health-check.
- **Tests E2E:** 3 usuarios con `max_browsers=1` terminan; solo las sesiones con hallazgos conservan
  screenshot/video; `/crash` provocado ⇒ hallazgo `server_crash`.

### Fase 9 — Reporter
- Dedupe por fingerprint, orden por severidad, pasos de reproducción, payload exacto,
  evidencia de consola/red, sugerencia de arreglo, bloque "Prompt para tu agente".
- **Tests:** dedupe; snapshot del Markdown generado a partir de una BD de fixture (timestamps
  normalizados) — determinista byte a byte.

### Fase 10 — IA opcional
- Proveedores + caché en SQLite; `normal_user` puede pedir datos realistas; el reporter puede pedir
  un resumen ejecutivo. Si la IA falla ⇒ fallback silencioso a heurísticas.
- **Tests:** `claude_cli` con `subprocess.run` mockeado; `ollama`/`anthropic`/`openai` con
  `httpx.MockTransport`; caché evita segunda llamada; JSON inválido ⇒ fallback.

### Fase 11 — Dashboard
- FastAPI + WebSocket con tail de SQLite, estáticos vanilla, reproductor de capturas/videos.
- **Tests:** `TestClient`: `/api/runs` lista runs; `/ws` recibe eventos insertados después de
  conectar; `/artifacts` no permite salir del directorio (traversal propio).

### Fase 12 — CLI y doctor
- `attack`, `dashboard`, `report`, `runs`, `doctor`, `demo`; código de salida según `--fail-on`.
- **Tests:** `CliRunner`: `--help`, `doctor` con chequeos mockeados, `attack` contra host remoto
  sin `--allow-remote` ⇒ error, `report` genera archivo.

### Fase 13 — Modo API
- Si el target expone `openapi.json` (o `--openapi`), fuzzeo de endpoints con httpx (valores
  válidos/borde/payloads) además del navegador.
- **Tests:** contra `/openapi.json` de la buggy app ⇒ `http_5xx` en `/api/items/{id}`.

### Fase 14 — Distribución y CI
- Scripts de instalación (Windows / Linux / Kali con PEP 668 ⇒ pipx o venv), README final,
  GitHub Actions (ruff + unit en Windows/Ubuntu, E2E en Ubuntu).

## Estado (v0.1.0)

| Fase | Estado | Tests |
|---|---|---|
| 0 Andamiaje | ✅ | `test_smoke.py` |
| 1 Config y seguridad | ✅ | `test_config_safety.py` |
| 2 Almacenamiento | ✅ | `test_store.py` |
| 3 Buggy app | ✅ | `test_buggy_app.py` |
| 4 Detectores | ✅ | `test_detectors.py` |
| 5 Personas (lógica) | ✅ | `test_personas.py` |
| 6-8 Navegador, personas, orquestador | ✅ | `test_e2e.py` |
| 9 Reporter (golden) | ✅ | `test_reporter.py` + `tests/golden/` |
| 10 IA opcional | ✅ | `test_ai.py` |
| 11 Dashboard | ✅ | `test_web.py` |
| 12 CLI y doctor | ✅ | `test_cli.py` |
| 13 Modo API | ✅ | `test_api_fuzzer.py` |
| 14 Distribución y CI | ✅ | `.github/workflows/ci.yml` |

## 3. Presupuesto de RAM (8 GB)

| Componente | Aprox. |
|---|---|
| Chromium headless (proceso base) | 150-250 MB |
| Cada contexto activo | 40-120 MB (+~30 MB con video) |
| Python + FastAPI | ~80 MB |
| **Defecto (`--max-browsers 2`)** | **≈ 0.5 GB** |

`--users` puede ser mayor que `--max-browsers`: los agentes esperan turno en el semáforo.

## 4. Fuera de alcance (v0.1)
- Escaneo de hosts de terceros, autenticación compleja (OAuth/2FA), carga masiva / DoS.
  ghost-twin **no** es una herramienta de pentest contra sistemas ajenos.
