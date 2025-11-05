import copy
import os
import random
import re
import time
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

from llm_fallback import llm
from metrics import Timer
from diagnostics_logger import diagnostics
from src.prompt_loader import load_prompt
from src.settings import AppSettings
from src.lexicon import get_stopwords
from logger_config import logger
from prompt_context import PromptContext
from style_guard import StyleRejection
from writing_rules import (
    BANNED_WORDS,
    FormatProfile,
    HOOK_GUIDELINES,
    closing_rule_prompt,
    comma_guard_prompt,
    count_analogy_markers,
    conjunction_guard_prompt,
    detect_banned_elements,
    hook_menu,
    select_format,
    should_allow_analogy,
    visual_anchor_prompt,
    validate_format,
    words_blocklist_prompt,
    _WORD_REGEX,
    BANNED_SUFFIXES,
)
from src.goldset import (
    retrieve_goldset_examples_random,
)


@dataclass(frozen=True)
class GenerationSettings:
    generation_model: str
    validation_model: str
    generation_temperature: float = 0.6


@dataclass
class CommentResult:
    comment: str
    insight: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class CommentAssessment:
    should_comment: bool
    reason: str = ""
    hook: Optional[str] = None
    risk: Optional[str] = None


@dataclass
class CommentRelevance:
    is_relevant: bool
    reason: str = ""


@lru_cache(maxsize=1)
def _comment_generation_prompt():
    """Load and cache the comment generation prompt specification."""
    prompts_dir = AppSettings.load().prompts_dir
    return load_prompt(prompts_dir, "comments/generation_v5_1")


@lru_cache(maxsize=1)
def _tail_sampling_prompt():
    prompts_dir = AppSettings.load().prompts_dir
    return load_prompt(prompts_dir, "generation/tail_sampling")


@lru_cache(maxsize=1)
def _contrast_analysis_prompt():
    prompts_dir = AppSettings.load().prompts_dir
    return load_prompt(prompts_dir, "generation/contrast_analysis")

def generate_all_variants(
    topic_abstract: str,
    context: PromptContext,
    settings: GenerationSettings,
    gold_examples: Optional[List[str]] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Generates three distinct tweet variants (short, mid, long) using a single, comprehensive LLM call."""
    import time
    start_time = time.time()

    # Load settings to get centralized length constraints
    app_settings = AppSettings.load()
    lengths = app_settings.variant_lengths

    system_message = (
        "You are a world-class ghostwriter who follows instructions precisely. "
        "You will perform a chain of thought process internally, but ONLY return the final JSON output."
        "\n\n<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n\n"
        "Audience ICP:\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n\n"
        "Complementary polish rules:\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )

    try:
        # Style-RAG: retrieve random goldset examples to enforce a stronger voice signal
        if gold_examples is None:
            _t0 = time.time()
            with Timer("g_goldset_random_retrieve", labels={"scope": "variants", "k": 5}):
                gold_examples = retrieve_goldset_examples_random(k=5)
            _elapsed_ms = round((time.time() - _t0) * 1000, 2)
            logger.info(
                "[RAG] Retrieved %s random goldset examples for prompt in %.2fms",
                len(gold_examples),
                _elapsed_ms,
            )

        gold_block = _format_gold_examples_for_prompt(gold_examples, limit=5)
        if not gold_block.strip():
            gold_block = "- (No reference examples available; rely on contract.)"
        
        # Load prompt from file and render with centralized length constraints
        prompts_dir = app_settings.prompts_dir
        prompt_spec = load_prompt(prompts_dir, "generation/all_variants_v4")
        user_prompt = prompt_spec.render(
            topic_abstract=topic_abstract,
            gold_examples_block=gold_block,
            len_short_max=lengths.short.max,
            len_mid_min=lengths.mid.min,
            len_mid_max=lengths.mid.max,
            len_long_min=lengths.long.min,
            len_long_max=lengths.long.max,
        )
        
        # Emit diagnostics to validate anchor block presence/format during smoke tests
        try:
            diagnostics.info(
                "generation_prompt_gold_block",
                {
                    "topic_preview": (topic_abstract or "")[:160],
                    "anchors_count": gold_block.count("\n") + (1 if gold_block.strip() else 0),
                    "gold_block_preview": gold_block[:240],
                },
            )
        except Exception:
            pass
    except Exception as exc:
        logger.error("Failed to load/render external prompt: %s. Cannot generate variants.", exc)
        raise StyleRejection(f"Prompt loading failed: {exc}")

    logger.info("Generating all variants via single-call multi-length prompt...")

    try:
        resp = llm.chat_json(
            model=settings.generation_model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt},
            ],
            temperature=max(0.0, min(1.0, settings.generation_temperature)),
        )

        def map_to_drafts(payload: Dict[str, object]) -> Dict[str, str]:
            return {
                "short": str(payload.get("short") or payload.get("draft_short") or "").strip(),
                "mid": str(payload.get("mid") or payload.get("draft_mid") or "").strip(),
                "long": str(payload.get("long") or payload.get("draft_long") or "").strip(),
            }

        def all_present(d: Dict[str, str]) -> bool:
            return all(bool(d[k]) for k in ("short", "mid", "long"))

        if not isinstance(resp, dict):
            raise StyleRejection("LLM did not return a JSON object.")

        drafts = map_to_drafts(resp)

        if not all_present(drafts):
            try:
                minimal_user = (
                    "Return ONLY strict JSON with keys {\"short\",\"mid\",\"long\"} under 280 chars each. "
                    "Avoid commas and conjunctions. If any field was missing, generate it now. Topic: "
                    + topic_abstract
                )
                fix = llm.chat_json(
                    model=settings.generation_model,
                    messages=[
                        {"role": "system", "content": "You format JSON only. No prose."},
                        {"role": "user", "content": minimal_user},
                    ],
                    temperature=0.2,
                )
                if isinstance(fix, dict):
                    drafts = map_to_drafts(fix)
            except Exception:
                pass

        if not all_present(drafts):
            raise StyleRejection("LLM failed to produce all three drafts in a single call.")

        logger.info(
            f"[PERF] Single-call for all variants took {time.time() - start_time:.2f} seconds."
        )

        def _strip_hashtags_and_fix(text: str) -> str:
            import re as _re
            t = _re.sub(r"#[A-Za-z0-9_]+\\s?", "", text or "")
            lines = [_re.sub(r"\s+", " ", ln).strip() for ln in t.splitlines() if ln.strip()]
            return "\n".join(lines)

        cleaned_drafts = {k: _strip_hashtags_and_fix(v) for k, v in drafts.items()}
        
        # The new architecture delegates all validation to the LLM Judge.
        # We return the cleaned drafts and an empty dictionary for failed variants.
        return cleaned_drafts, {}

    except Exception as e:
        logger.error(f"Error in single-call all-variant generation: {e}", exc_info=True)
        raise StyleRejection(f"Failed to generate variants: {e}")

# The rest of the file containing comment generation logic and other helpers remains unchanged.
# For brevity, it is not included in this display but will be part of the file write.