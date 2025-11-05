# Telemetría: Contrato y Política de Fallos

Autor: M. Mei
Fecha: 2025-11-05
Propósito: Introducir un wrapper de telemetría a prueba de bombas y documentar su uso
Justificación: Evitar que problemas del backend de telemetría bloqueen el flujo de usuarios o pipelines

## Resumen

Este documento especifica el contrato estable de la API de telemetría y su política de fallos. La implementación provee `safe_capture(event, payload)` que, por defecto, nunca rompe el pipeline si el backend de telemetría falla.

## API estable

- Telemetry.capture(event: str, payload: dict | None) -> None
- safe_capture(event: str, payload: dict | None) -> None

El wrapper `safe_capture` debe ser el punto de entrada recomendado en todo el código de aplicación.

## Variables de entorno

- `TELEMETRY_STRICT` (default: `0`)
  - `0` / `false` / `no`: Modo no estricto. Las excepciones se tragan y se emite un warning estructurado `TELEM_CAPTURE_FAILED`.
  - `1` / `true` / `yes`: Modo estricto. Se emite un error estructurado `TELEM_CAPTURE_HARD_FAIL` y se re-lanza la excepción.

## Semántica de fallos

- Fallo de backend / red / cliente de telemetría:
  - No estricto: No impacta el flujo (best-effort). Se registra el evento y el detalle del error.
  - Estricto: El fallo es visible y puede romper el flujo intencionalmente para detectar problemas temprano.

## Ejemplos de uso

```python
from src.telemetry import safe_capture

safe_capture("USER_SIGNUP", {"user_id": uid, "plan": plan})
```

Con modo estricto activado:

```bash
export TELEMETRY_STRICT=1
python app.py
```

## Integración y cableado del cliente

Por defecto se utiliza `NoOpTelemetryClient` para no introducir dependencias duras. En despliegues, se puede sustituir por un cliente real (p. ej. Segment, PostHog, OpenTelemetry) asignando:

```python
from src.telemetry import TELEMETRY, Telemetry
from my_client_impl import MyTelemetryClient

TELEMETRY = Telemetry(client=MyTelemetryClient(...))
```

## Registro estructurado

Los fallos de captura se emiten vía `diagnostics` con eventos:

- `TELEM_CAPTURE_FAILED` (warning)
- `TELEM_CAPTURE_HARD_FAIL` (error)

## Cambios documentados

- 2025-11-05 — Autor: M. Mei — Propósito: Añadir wrapper `safe_capture` y documentación — Justificación: Protección del flujo ante fallos de telemetría.

