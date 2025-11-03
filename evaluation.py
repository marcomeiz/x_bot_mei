import os
import yaml
from typing import Dict, Optional, Any

from llm_fallback import llm
from logger_config import logger
from prompt_context import PromptContext

# --- Constants & Config Loading ---

CONFIDENCE_THRESHOLD = float(os.getenv("EVAL_CONFIDENCE_THRESHOLD", "4.5"))

def load_rubric(file_path: str) -> Dict[str, Any]:
    """Loads a rubric from a YAML file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load rubric {file_path}: {e}")
        return {}

FAST_RUBRIC_PATH = os.path.join(os.path.dirname(__file__), "config", "evaluation_fast.yaml")
SLOW_RUBRIC_PATH = os.path.join(os.path.dirname(__file__), "config", "evaluation_slow.yaml")

FAST_RUBRIC = load_rubric(FAST_RUBRIC_PATH)
SLOW_RUBRIC = load_rubric(SLOW_RUBRIC_PATH)

# --- Core Evaluation Functions ---

def _run_evaluation(text: str, context: PromptContext, rubric: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Runs a single evaluation pass against a given rubric."""
    if not rubric or not text:
        return None

    model = rubric.get("model", "x-ai/grok-4-fast")
    rubric_text = yaml.dump(rubric.get("rubric", {}))
    output_format_text = yaml.dump(rubric.get("output_format", {}))

    prompt = f"""
    You are a ruthless editor reviewing a tweet draft. Evaluate it strictly against the following rubric.

    **DRAFT:**
    ---
    {text}
    ---

    **RUBRIC:**
    {rubric_text}

    **OUTPUT FORMAT (Strict JSON only):**
    {output_format_text}
    """

    system_message = (
        "You are a deterministic evaluation expert. Respond ONLY with strict JSON based on the provided format.\n\n"
        "<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n\n"
        "<ICP>\n"
        + context.icp
        + "\n</ICP>"
    )

    try:
        payload = llm.chat_json(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        return payload if isinstance(payload, dict) else None
    except Exception as e:
        logger.warning(f"Evaluation pass '{rubric.get('name', 'unknown')}' failed: {e}")
        return None

def evaluate_draft(text: str, context: PromptContext) -> Optional[Dict[str, Any]]:
    """Orchestrates the two-step evaluation process (fast then slow)."""
    if not text:
        return None

    # --- Fast Judge ---
    logger.info("Running Fast Evaluation...")
    fast_result = _run_evaluation(text, context, FAST_RUBRIC)
    if not fast_result:
        logger.warning("Fast evaluation failed to return a result.")
        return {"error": "Fast evaluation failed."}

    # Calculate average score from fast evaluation
    fast_scores = [v for k, v in fast_result.items() if k.endswith("_score") and isinstance(v, (int, float))]
    avg_fast_score = sum(fast_scores) / len(fast_scores) if fast_scores else 0

    # Adaptive Confidence Check
    if avg_fast_score >= CONFIDENCE_THRESHOLD:
        logger.info(f"Fast evaluation passed with high confidence ({avg_fast_score:.2f} >= {CONFIDENCE_THRESHOLD}). Skipping slow evaluation.")
        # Combine results and return
        final_result = {"fast_eval": fast_result, "slow_eval_skipped": True, "final_score": avg_fast_score}
        return final_result

    # --- Slow Judge ---
    logger.info("Running Slow Evaluation...")
    slow_result = _run_evaluation(text, context, SLOW_RUBRIC)
    if not slow_result:
        logger.warning("Slow evaluation failed to return a result.")
        # Return fast results anyway, as they are better than nothing
        return {"fast_eval": fast_result, "error": "Slow evaluation failed."}

    # --- Combine and Log ---
    # For now, just return both results. The comparative logging will be handled by the calling service.
    final_result = {"fast_eval": fast_result, "slow_eval": slow_result, "slow_eval_skipped": False}
    
    # Here you would add the logic for comparative logging (e.g., send to a specific log sink or database)
    logger.info(f"Comparative Log: Fast({avg_fast_score:.2f}) vs Slow({slow_result})")

    return final_result
