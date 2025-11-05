"""Telemetry module providing a safe_capture wrapper that never breaks the pipeline by default.

Contract:
- Telemetry.capture(event: str, payload: dict | None) -> None
- safe_capture(event: str, payload: dict | None) -> None

Failure policy controlled by env TELEMETRY_STRICT:
- TELEMETRY_STRICT=0 (default): swallow exceptions, emit structured warning TELEM_CAPTURE_FAILED
- TELEMETRY_STRICT=1: emit structured error TELEM_CAPTURE_HARD_FAIL and re-raise
"""

from __future__ import annotations

import os
from typing import Optional, Dict, Any

from diagnostics_logger import diagnostics


class TelemetryClient:
    """Interface base para clientes de telemetría."""

    def capture(self, event: str, payload: Dict[str, Any] | None = None) -> None:  # pragma: no cover
        raise NotImplementedError


class NoOpTelemetryClient(TelemetryClient):
    """Cliente de telemetría sin efectos (no-op). Seguro para desarrollo/pruebas."""

    def capture(self, event: str, payload: Dict[str, Any] | None = None) -> None:
        # No hace nada, permite que el pipeline continúe
        return None


class Telemetry:
    def __init__(self, client: TelemetryClient):
        self.client = client

    def capture(self, event: str, payload: Dict[str, Any] | None = None) -> None:
        self.client.capture(event, payload or {})


# Instancia global. Se puede cablear un cliente real en despliegues.
TELEMETRY = Telemetry(client=NoOpTelemetryClient())


def is_strict() -> bool:
    """Determina si el modo estricto está habilitado via env."""
    val = os.getenv("TELEMETRY_STRICT", "0").strip().lower()
    return val in {"1", "true", "yes"}


def safe_capture(event: str, payload: Dict[str, Any] | None = None) -> None:
    """Captura eventos de telemetría sin romper el flujo bajo fallos.

    Politica de fallo:
    - Estricto deshabilitado (por defecto): registra warning y traga excepción
    - Estricto habilitado: registra error y re-lanza
    """
    try:
        TELEMETRY.capture(event, payload or {})
    except Exception as e:  # pragma: no cover
        detail = {"event": event, "err": str(e)}
        if is_strict():
            diagnostics.error("TELEM_CAPTURE_HARD_FAIL", detail)
            raise
        else:
            diagnostics.warn("TELEM_CAPTURE_FAILED", detail)

