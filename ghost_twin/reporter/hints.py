"""Deterministic fix suggestions per bug type (no AI needed)."""

from __future__ import annotations

HINTS: dict[str, dict[str, str]] = {
    "server_crash": {
        "en": "The process died or stopped accepting connections. Check the server logs around "
              "the last actions listed above, wrap request handlers so one failing request cannot "
              "kill the process, and run the server under a supervisor that restarts it.",
        "es": "El proceso murió o dejó de aceptar conexiones. Revisa los logs del servidor en torno "
              "a las últimas acciones listadas, envuelve los handlers para que una petición fallida "
              "no tumbe el proceso y ejecútalo con un supervisor que lo reinicie.",
    },
    "sql_error_leak": {
        "en": "Use parameterized queries / the ORM's query builder instead of string "
              "concatenation, and never return raw database errors to the client (log them "
              "server-side, answer a generic 4xx/5xx).",
        "es": "Usa consultas parametrizadas / el query builder del ORM en lugar de concatenar "
              "strings, y nunca devuelvas errores crudos de la base de datos al cliente (regístralos "
              "en el servidor y responde un 4xx/5xx genérico).",
    },
    "path_traversal": {
        "en": "Never build filesystem paths from user input. Map identifiers to an allow-list, or "
              "resolve the path and verify it stays inside the base directory "
              "(e.g. `Path(base, name).resolve().is_relative_to(base)`).",
        "es": "Nunca construyas rutas del sistema de archivos con input del usuario. Usa una lista "
              "blanca de identificadores, o resuelve la ruta y verifica que quede dentro del "
              "directorio base (p. ej. `Path(base, name).resolve().is_relative_to(base)`).",
    },
    "reflected_xss": {
        "en": "Escape user input on output (use the template engine's auto-escaping, avoid "
              "`innerHTML`/`dangerouslySetInnerHTML`/`|safe` with user data) and add a "
              "Content-Security-Policy.",
        "es": "Escapa el input del usuario al renderizar (usa el auto-escape del motor de plantillas, "
              "evita `innerHTML`/`dangerouslySetInnerHTML`/`|safe` con datos del usuario) y añade "
              "una Content-Security-Policy.",
    },
    "http_5xx": {
        "en": "Validate input at the boundary (schema validation) and return 4xx for bad input; "
              "add error handling so unexpected states don't surface as 500s.",
        "es": "Valida el input en la frontera (validación por esquema) y devuelve 4xx ante datos "
              "inválidos; añade manejo de errores para que estados inesperados no terminen en 500.",
    },
    "js_exception": {
        "en": "Guard against missing/undefined values (optional chaining, default values), check "
              "`response.ok` before using fetched data and add an error boundary.",
        "es": "Protégete de valores ausentes/undefined (optional chaining, valores por defecto), "
              "comprueba `response.ok` antes de usar datos de un fetch y añade un error boundary.",
    },
    "console_error": {
        "en": "Find where this message is logged and fix the underlying condition (missing config, "
              "failed request, bad state) instead of silencing the log.",
        "es": "Encuentra dónde se registra este mensaje y corrige la causa (config ausente, petición "
              "fallida, estado inválido) en lugar de silenciar el log.",
    },
    "double_submit": {
        "en": "Disable the submit control while the request is in flight and make the endpoint "
              "idempotent (idempotency key or unique constraint).",
        "es": "Deshabilita el botón de envío mientras la petición está en curso y haz el endpoint "
              "idempotente (idempotency key o restricción única).",
    },
    "slow_response": {
        "en": "Profile this endpoint: look for N+1 queries, missing indexes, blocking I/O or work "
              "that can be cached or moved to a background job.",
        "es": "Perfila este endpoint: busca consultas N+1, índices faltantes, I/O bloqueante o "
              "trabajo que se pueda cachear o mover a un job en segundo plano.",
    },
    "http_4xx": {
        "en": "A resource the page depends on is missing or the route is wrong. Fix the path or "
              "the route registration.",
        "es": "Falta un recurso del que depende la página o la ruta es incorrecta. Corrige la ruta "
              "o el registro del endpoint.",
    },
    "request_failed": {
        "en": "The request never completed (connection reset/refused, CORS, etc.). Check the server "
              "logs at that time and the network configuration.",
        "es": "La petición nunca terminó (conexión reiniciada/rechazada, CORS, etc.). Revisa los "
              "logs del servidor en ese momento y la configuración de red.",
    },
}


def hint_for(kind: str, lang: str = "en") -> str:
    h = HINTS.get(kind)
    if not h:
        return ""
    return h.get(lang) or h["en"]
