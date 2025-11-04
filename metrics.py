"""
Módulo de métricas y monitoreo.

Captura KPIs:
- Tiempo de respuesta (ms)
- Uso de memoria (MB, best-effort)
- Precisión (placeholder: valores de similitud/coverage si aplica)
- Estabilidad (errores/exception count)

Salida: logs estructurados vía logger (Cloud Logging friendly).
"""

import json
import time
import os
from typing import Optional, Dict, Any
from logger_config import logger

_use_psutil = False
try:
    import psutil  # optional
    _use_psutil = True
except Exception:
    _use_psutil = False


def _get_memory_mb() -> Optional[float]:
    try:
        if _use_psutil:
            process = psutil.Process()
            rss = process.memory_info().rss
            return round(rss / (1024 * 1024), 2)
    except Exception:
        pass
    return None


def record_metric(name: str, value: float, labels: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        "metric": name,
        "value": value,
        "ts": int(time.time()),
    }
    if labels:
        payload.update(labels)
    try:
        logger.info("[METRIC] %s", json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.info("[METRIC] %s=%s labels=%s", name, value, labels)


class Timer:
    def __init__(self, name: str, labels: Optional[Dict[str, Any]] = None):
        self.name = name
        self.labels = labels or {}
        self.start = None

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        end = time.time()
        ms = round((end - (self.start or end)) * 1000, 2)
        mem = _get_memory_mb()
        labels = dict(self.labels)
        if mem is not None:
            labels["mem_mb"] = mem
        labels["error"] = bool(exc_type)
        record_metric(self.name, ms, labels)

