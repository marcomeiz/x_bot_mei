"""Long-first pipeline with early evaluation and parallel derivation of variants.

States:
- LONG_GENERATE: Generate exactly one LONG draft with max quality settings.
- LONG_EVAL: Evaluate LONG early; abort if fails (circuit breaker).
- VARIANTS_FROM_LONG: Derive MID and SHORT from approved LONG, in parallel.
- VARIANT_EVAL: Evaluate MID and SHORT with format-specific thresholds; keep any that pass.

Metrics are emitted via diagnostics_logger and latencies measured per stage.
Positive evaluations are cached via eval_cache to skip repeated scoring.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Optional

from diagnostics_logger import diagnostics
from prompt_context import PromptContext
from src.settings import AppSettings
from variant_generators import (
    GenerationSettings,
    regenerate_single_variant,
    compress_to_short,
    compress_to_mid,
)
from evaluation import evaluate_draft, CONFIDENCE_THRESHOLD
from eval_cache import eval_cache


def _threshold(label: str) -> float:
    base = CONFIDENCE_THRESHOLD
    if label == "long":
        return float(os.getenv("LONG_CONFIDENCE_THRESHOLD", str(base)) or base)
    if label == "mid":
        return float(os.getenv("MID_CONFIDENCE_THRESHOLD", str(base)) or base)
    if label == "short":
        return float(os.getenv("SHORT_CONFIDENCE_THRESHOLD", str(base)) or base)
    return base


def _approved(ev: Optional[Dict[str, Any]], label: str) -> tuple[bool, float]:
    if not isinstance(ev, dict):
        return False, 0.0
    fast = ev.get("fast_eval") or {}
    fast_scores = [v for k, v in fast.items() if str(k).endswith("_score") and isinstance(v, (int, float))]
    avg_fast = sum(fast_scores) / len(fast_scores) if fast_scores else 0.0
    # If final_score exists (fast pass), use that
    avg_fast = float(ev.get("final_score", avg_fast) or avg_fast)
    return avg_fast >= _threshold(label), avg_fast


def run_long_first_pipeline(topic_abstract: str, context: PromptContext) -> Dict[str, Any]:
    start_total = time.time()
    app = AppSettings.load()
    settings = GenerationSettings(
        generation_model=app.post_model,
        validation_model=app.eval_fast_model,
        generation_temperature=app.post_temperature,
    )

    result: Dict[str, Any] = {
        "long": "",
        "mid": "",
        "short": "",
        "stage_latencies": {},
        "evaluations": {},
        "errors": {},
        "pipeline_version": os.getenv("PIPELINE_VERSION", "long_first_v1"),
    }

    # --- LONG_GENERATE ---
    t0 = time.time()
    diagnostics.info("LONG_GENERATE_start", {"model": app.post_model})
    long_text = regenerate_single_variant("long", topic_abstract, context, settings)
    latency = time.time() - t0
    result["stage_latencies"]["LONG_GENERATE"] = latency
    if not long_text:
        result["errors"]["LONG_GENERATE"] = "No se pudo generar el LONG."
        diagnostics.error("LONG_GENERATE_fail", {"latency": latency})
        return result
    result["long"] = long_text
    diagnostics.info("LONG_GENERATE_ok", {"latency": latency, "chars": len(long_text or "")})

    # --- LONG_EVAL --- (with cache)
    t1 = time.time()
    cached = eval_cache.get(long_text)
    if cached:
        approved = True
        avg_fast = float(cached.get("avg_fast_score", 0.0) or 0.0)
        ev_long = {"fast_eval": {"avg_fast_score": avg_fast}, "slow_eval_skipped": True, "final_score": avg_fast}
        diagnostics.info("LONG_EVAL_cache_hit", {"avg_fast": avg_fast})
    else:
        diagnostics.info("LONG_EVAL_start", {"threshold": _threshold("long")})
        ev_long = evaluate_draft(long_text, context)
        approved, avg_fast = _approved(ev_long, "long")
        if approved:
            eval_cache.put(long_text, {"avg_fast_score": avg_fast}, approved=True)
    result["stage_latencies"]["LONG_EVAL"] = time.time() - t1
    result["evaluations"]["long"] = ev_long
    if not approved:
        result["errors"]["LONG_EVAL"] = "El LONG no alcanzó el umbral de calidad."
        diagnostics.warn(
            "LONG_EVAL_reject",
            {"avg_fast": avg_fast, "threshold": _threshold("long"), "latency": result["stage_latencies"]["LONG_EVAL"]},
        )
        # Circuit breaker: abort
        result["stage_latencies"]["TOTAL"] = time.time() - start_total
        diagnostics.info("PIPELINE_abort", {"stage": "LONG_EVAL"})
        return result

    # --- VARIANTS_FROM_LONG --- (parallel)
    t2 = time.time()
    diagnostics.info("VARIANTS_FROM_LONG_start", {"source_len": len(long_text or "")})
    produced: Dict[str, str] = {"mid": "", "short": ""}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_map = {
            pool.submit(compress_to_mid, long_text, app.post_refiner_model): "mid",
            pool.submit(compress_to_short, long_text, app.post_refiner_model): "short",
        }
        for fut in as_completed(future_map):
            label = future_map[fut]
            try:
                produced[label] = (fut.result() or "").strip()
                diagnostics.info("VARIANT_DERIVE_ok", {"variant": label, "chars": len(produced[label])})
            except Exception as exc:
                result["errors"][f"VARIANTS_FROM_LONG_{label}"] = f"Error derivando {label}: {exc}"
                diagnostics.warn("VARIANT_DERIVE_fail", {"variant": label, "error": str(exc)})
    result["stage_latencies"]["VARIANTS_FROM_LONG"] = time.time() - t2
    result.update(produced)

    # --- VARIANT_EVAL --- (independent)
    t3 = time.time()
    diagnostics.info("VARIANT_EVAL_start", {"thresholds": {"mid": _threshold("mid"), "short": _threshold("short")}})
    kept_any = False
    for label in ("mid", "short"):
        text = result.get(label) or ""
        if not text:
            continue
        cached = eval_cache.get(text)
        if cached:
            approved = True
            avg_fast = float(cached.get("avg_fast_score", 0.0) or 0.0)
            ev = {"fast_eval": {"avg_fast_score": avg_fast}, "slow_eval_skipped": True, "final_score": avg_fast}
            diagnostics.info("VARIANT_EVAL_cache_hit", {"variant": label, "avg_fast": avg_fast})
        else:
            ev = evaluate_draft(text, context)
            approved, avg_fast = _approved(ev, label)
            if approved:
                eval_cache.put(text, {"avg_fast_score": avg_fast}, approved=True)
        result["evaluations"][label] = ev
        if not approved:
            # Fallback: remove failed variant but keep the other if approved
            diagnostics.warn("VARIANT_EVAL_reject", {"variant": label, "avg_fast": avg_fast, "threshold": _threshold(label)})
            result[label] = ""
        else:
            kept_any = True
    result["stage_latencies"]["VARIANT_EVAL"] = time.time() - t3

    result["stage_latencies"]["TOTAL"] = time.time() - start_total
    diagnostics.info("PIPELINE_success", {
        "kept_mid": bool(result.get("mid")),
        "kept_short": bool(result.get("short")),
        "latencies": result["stage_latencies"],
    })
    if not kept_any:
        result["errors"]["VARIANT_EVAL"] = "Ninguna variante aprobó; pipeline terminó sin resultados publicables."
    return result

