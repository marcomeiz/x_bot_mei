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





STOPWORDS = get_stopwords()

HEDGING_REGEX = re.compile(
    r"\b(i think|maybe|probably|seems|appears|kind of|sort of|in my opinion|i feel|could|might)\b",
    re.I,
)
CLICHE_REGEX = re.compile(
    r"\b(let'?s dive in|game[- ]?changing|unlock(ing)? potential|revolutionary|cutting[- ]edge|synergy|disruption|leverage|empower|unleash|10x|next[- ]level|paradigm|world[- ]class|best[- ]in[- ]class)\b",
    re.I,
)
HYPE_REGEX = re.compile(
    r"\b(guarantee|instant|effortless|secret sauce|never fail|zero risk|magic|overnight)\b",
    re.I,
)
NON_ENGLISH_CHARS = re.compile(r"[áéíóúñüÁÉÍÓÚÑÜ]")
# Heurística adicional para detectar español sin acentos (casos ASCII)
SPANISH_HINTS = {
    # Stopwords y partículas comunes
    "de", "la", "el", "y", "que", "en", "no", "se", "los", "por", "un", "una",
    "para", "con", "del", "las", "como", "le", "lo", "su", "al", "más", "si", "ya",
    "muy", "pero", "porque", "cuando", "donde", "sobre",
    # Palabras de uso frecuente en nuestros ejemplos
    "tiempo", "bloquea", "trabajo", "reuniones", "haz", "ahora",
}
END_PUNCT = re.compile(r"[.!?]$")































TAIL_SAMPLING_COUNT = int(os.getenv("TAIL_SAMPLING_COUNT", "3") or 3)

REVIEWER_PROFILES: List[Dict[str, str]] = [
    {
        "name": "Contrarian Reviewer",
        "role": (
            "You are obsessed with tail-distribution insights. You hate safe takes and call out any mainstream phrasing. "
            "Your job is to force the writer to lead with a bold, uncomfortable truth that still fits the ICP."
        ),
        "focus": (
            "Identify where the draft slips into common wisdom. Suggest one sharper, lower-probability angle or detail that hits harder."
        ),
    },
    {
        "name": "Compliance Reviewer",
        "role": (
            "You enforce the COOlogy style contract. No hedging, no bloated sentences, voice must stay NYC bar sharp."
        ),
        "focus": (
            "Point the exact spots where tone drifts, verbs weaken, or the contract/ICP are violated. Offer a direct correction."
        ),
    },
    {
        "name": "Clarity Reviewer",
        "role": (
            "You are ruthless about clarity and specificity. If something sounds abstract, you demand a concrete example or metric."
        ),
        "focus": (
            "Highlight vague claims, missing metrics, or fuzzy stakes. Suggest what tangible detail would make it undeniable."
        ),
    },
]

SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")




















# --- Adaptive helpers (lightweight rewrites) ---









def _limit_lines(text: str, max_lines: int = 2) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return text.strip()
    if max_lines <= 0:
        return " ".join(lines)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    if max_lines == 1:
        return " ".join(lines)
    first = lines[0]
    second = " ".join(lines[1:])
    return "\n".join([first, second])


def _enforce_line_limit(text: str, max_lines: int = 2) -> str:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return text.strip()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    if max_lines == 1:
        return " ".join(lines)
    first = lines[0]
    second = " ".join(lines[1:])
    return "\n".join([first, second])


def _limit_sentences(text: str, max_sentences: int = 2) -> str:
    if max_sentences <= 0:
        return text.strip()
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [p.strip() for p in parts if p.strip()]
    if not sentences:
        return text.strip()
    clipped = sentences[:max_sentences]
    if len(clipped) == 1:
        return clipped[0]
    return "\n".join(clipped)


def _normalize_token(token: str) -> str:
    word = token.lower().strip(".,!?\"'()[]{}")
    if len(word) <= 2:
        return ""
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("ing") and len(word) > 4:
        word = word[:-3]
    elif word.endswith("ied") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("ed") and len(word) > 3:
        word = word[:-2]
    elif word.endswith("es") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("s") and len(word) > 3:
        word = word[:-1]
    return word


def _normalized_token_set(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z']+", text.lower())
    normalized = set()
    for token in tokens:
        norm = _normalize_token(token)
        if norm and len(norm) >= 3 and norm not in STOPWORDS:
            normalized.add(norm)
    return normalized


def _validate_comment_relevance(
    source_excerpt: str,
    comment_text: str,
    context: PromptContext,
    model: str,
    key_terms: Optional[List[str]] = None,
) -> CommentRelevance:
    prompt = f"""
We only want to reply if the comment clearly references the post and adds value to operators/COO ICP.

POST (excerpt):
\"\"\"{source_excerpt}\"\"\"

COMMENT:
\"\"\"{comment_text}\"\"\"

Answer strictly as JSON:
{{
  "is_relevant": boolean,
  "reason": string (<=160 chars)
}}

Mark is_relevant=true ONLY if the comment references a concrete detail/tension from the post AND extends it with a meaningful operator-focused insight or question.
If the comment is vague, generic, or ignores the post's content, return false and explain why in reason.
"""
    if key_terms:
        prompt += "\nKey focus terms: " + ", ".join(key_terms[:6]) + "\n"
    system_message = (
        "You are a strict reviewer preventing spammy replies. Enforce relevance to the excerpt and ICP value.\n\n<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )
    try:
        data = llm.chat_json(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        if isinstance(data, dict):
            relevant = bool(data.get("is_relevant", False))
            reason = str(data.get("reason", "")).strip()[:160]
            return CommentRelevance(is_relevant=relevant, reason=reason)
    except Exception as exc:
        logger.warning("Could not validate comment relevance: %s", exc)
    return CommentRelevance(is_relevant=True, reason="Relevance not verified (fallback).")

def assess_comment_opportunity(
    source_text: str,
    context: PromptContext,
    settings: GenerationSettings,
) -> CommentAssessment:
    excerpt = _compact_text(source_text, limit=1200)
    prompt = f"""
We are deciding whether to reply to this post as a fractional COO ghostwriter whose north star is delivering value to operators.

Post excerpt:
\"\"\"{excerpt}\"\"\"

Answer in strict JSON with:
{{
  "should_comment": boolean,
  "reason": string (<=160 chars),
  "hook": string (<=160 chars, optional),
  "risk": string (<=160 chars, optional)
}}

Guidelines:
- should_comment = true ONLY if we can credibly add value for our ICP: tactical advice, challenge, or question that advances the conversation.
- Return false if the post is off-topic, purely self-promotional, or would force us into speculation.
- reason must be specific (e.g., "Author is venting about churn math—can share handoff cadence tip").
- If false, reason should state why (e.g., "Topic is crypto trading — outside ICP").
"""

    system_message = (
        "You are a strategist deciding whether to engage publicly. Protect the ICP focus and voice.\n\n<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )

    try:
        data = llm.chat_json(
            model=settings.generation_model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        if isinstance(data, dict):
            should = bool(data.get("should_comment", False))
            reason = str(data.get("reason", "")).strip()[:160]
            hook = str(data.get("hook", "")).strip()[:160] if data.get("hook") else None
            risk = str(data.get("risk", "")).strip()[:160] if data.get("risk") else None
            logger.info(
                "Comment assessment: should_comment=%s | reason=%s | hook=%s | risk=%s",
                should,
                reason,
                hook,
                risk,
            )
            return CommentAssessment(should_comment=should, reason=reason, hook=hook, risk=risk)
    except Exception as exc:
        logger.warning("Could not assess comment opportunity: %s", exc)

    return CommentAssessment(
        should_comment=True,
        reason="No LLM evaluation (fallback).",
    )


def _extract_key_terms(text: str, max_terms: int = 6) -> List[str]:
    tokens = re.findall(r"[A-Za-z']+", text.lower())
    seen: set[str] = set()
    key_terms: List[str] = []
    for token in tokens:
        if len(token) < 4:
            continue
        if token in STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        key_terms.append(token)
        if len(key_terms) >= max_terms:
            break
    return key_terms





def _build_system_message(context: PromptContext) -> str:
    return (
        "You are a world-class ghostwriter creating two tweet drafts. "
        "Return ONLY strict JSON with exactly two string properties: draft_a and draft_b.\n\n"
        "<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n\n"
        "Audience ICP:\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n\n"
        "Complementary polish rules (do not override the contract/ICP):\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )








def generate_all_variants(
    topic_abstract: str,
    context: PromptContext,
    settings: GenerationSettings,
    gold_examples: Optional[List[str]] = None,
) -> Dict[str, str]:


    """Generates three distinct tweet variants (short, mid, long) using a single, comprehensive LLM call."""


    import time


    start_time = time.time()





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





    user_prompt = f"""
    **Prime Directive: Clarity of Diagnosis > Poetic Creativity.**
    Your absolute priority is to be understood in 3 seconds. The goal is to provide a sharp, operational diagnosis, not a philosophical musing.

    **Metaphor & Analogy Rule: Concrete & Drawable ONLY.**
    - Any metaphor MUST be 100% concrete and visual (physical objects, tangible actions).
    - Any abstract or philosophical metaphor (e.g., "the grief of a fantasy," "sacrificing control") is a failure.
    - If you are in doubt, ALWAYS default to a literal, direct statement.

    Your task is to generate three distinct, high-quality tweet variants based on the provided topic, each with a different length and structural feel. Follow these steps internally:





    1.  **Analyze the Topic:**


        -   Topic: "{topic_abstract}"





    2.  **Internal Brainstorm (Chain of Thought):**


        -   Generate 1-2 contrarian or non-obvious angles for this topic.


        -   Select the strongest angle to use as the core theme for all three versions.





    3.  **Drafting (Internal Thought):**


                -   **Version A (The Surgical Diagnosis):** Write a 1-2 line knockout blow (≤150 characters). It must NOT be a vague positive statement. It MUST be a brutal, specific operational or financial diagnosis that attacks the ICP's failed math, false identity, or broken system. (Example: 'Stop being the highest-paid 

        0/hr employee in your own business.')


                -   **Version B (Standard):** Write a standard-length draft (180–220 characters) with a solid rhythm.


        -   **Version C (Extended):** Write a longer draft (240–280 characters) that tells a mini-story or ends with a strong, imperative call to action.


        -   Ensure all drafts adhere to the style contract (Hormozi cadence: short, one-sentence paragraphs, no hedging).





                4.  **Final Output:**





                    -   Return ONLY a strict JSON object with the three final, polished drafts.





                    -   All drafts MUST be in English. Adhere to this rule strictly.





            





            





            





            





            





                **CRITICAL OUTPUT FORMAT:**


    Return ONLY a strict JSON object with the following structure:


    {{


      "draft_short": "<Final polished text for the short version>",


      "draft_mid": "<Final polished text for the mid-length version>",


      "draft_long": "<Final polished text for the long version>"


    }}


    """





    # Override inline prompt with externalized template
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
        # Emit anchor count for visibility
        try:
            _anchors_count = gold_block.count("\n") + (1 if gold_block.strip() else 0)
            logger.info("[DIAG] anchors_count=%s", _anchors_count)
        except Exception:
            pass

        prompts_dir = AppSettings.load().prompts_dir
        user_prompt = load_prompt(prompts_dir, "generation/all_variants_v4").render(
            topic_abstract=topic_abstract,
            gold_examples_block=gold_block,
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
        # Fallback to the inline prompt above if loading fails
        logger.warning("Failed to load/render external prompt: %s. Using inline.", exc)

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

        # Accept both schemas: {short,mid,long} or {draft_short,draft_mid,draft_long}
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
            # Single, cheap retry to enforce schema
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

        # Raw mode: NO guardrails (return raw drafts for VOICE tuning)
        if SUSPEND_GUARDRAILS:
            return (
                {
                    "short": (drafts.get("short") or "").strip(),
                    "mid": (drafts.get("mid") or "").strip(),
                    "long": (drafts.get("long") or "").strip(),
                },
                {},
            )

        # NOTE: Warden Minimal (mechanical enforcement/regeneration) has been removed.
        # Generation returns raw drafts that are only lightly cleaned below.
        # All style/mechanical validation is now delegated to the LLM Judge
        # in proposal_service.py.

        # Hard gate: basic cleaning only (hashtags removal + space collapsing)
        def _strip_hashtags_and_fix(text: str) -> str:
            import re as _re
            # Remove hashtags; preserve line breaks; collapse internal whitespace
            t = _re.sub(r"#[A-Za-z0-9_]+", "", text or "")
            lines = []
            for ln in t.splitlines():
                # Collapse internal whitespace per line and strip ends
                norm = _re.sub(r"\s+", " ", ln).strip()
                if norm:
                    lines.append(norm)
            return "\n".join(lines)

        # NOTE: Mechanical repair and compliance enforcement removed per request.

        def _validate_variant(label: str, draft: str) -> str:
            draft = _strip_hashtags_and_fix(draft)
            # Post-process now only performs basic cleaning; all style validation is deferred
            # to proposal_service.py via the LLM Judge (_check_style_with_llm).
            return draft

        VARIANT_MAX_ATTEMPTS = 3

        def _clean_variant_with_retries(label: str, seed: str) -> Tuple[str, Optional[str]]:
            attempts = 0
            candidate = seed
            last_error = ""
            while attempts < VARIANT_MAX_ATTEMPTS:
                candidate_stripped = (candidate or "").strip()
                if candidate_stripped:
                    try:
                        return _validate_variant(label, candidate_stripped), None
                    except StyleRejection as err:
                        last_error = (str(err) or "").strip()
                        logger.warning(
                            "Variant %s failed validation (attempt %s/%s): %s",
                            label,
                            attempts + 1,
                            VARIANT_MAX_ATTEMPTS,
                            last_error,
                        )
                else:
                    last_error = "Missing content."
                    logger.warning("Variant %s missing content; attempting regeneration.", label)

                candidate = regenerate_single_variant(label, topic_abstract, context, settings)
                attempts += 1
                if candidate:
                    logger.info("Variant %s regenerated (attempt %s/%s).", label, attempts, VARIANT_MAX_ATTEMPTS)

            return "", (last_error or f"Variant {label} failed after {VARIANT_MAX_ATTEMPTS} attempts.").strip()

        cleaned: Dict[str, str] = {}
        failed_variants: Dict[str, str] = {}
        for label in ("short", "mid", "long"):
            text, err = _clean_variant_with_retries(label, drafts.get(label, ""))
            cleaned[label] = text
            if err:
                trimmed = err if len(err) <= 120 else err[:117] + "..."
                failed_variants[label] = trimmed

        return cleaned, failed_variants





    except Exception as e:


        logger.error(f"Error in single-call all-variant generation: {e}", exc_info=True)


        raise StyleRejection(f"Failed to generate variants: {e}")


def generate_comment_reply(
    source_text: str,
    context: PromptContext,
    settings: GenerationSettings,
    assessment: Optional[CommentAssessment] = None,
) -> CommentResult:
    """
    Generate a single conversational reply/comment anchored on the provided source text.
    The output keeps the ICP voice and ends with an invitation to continue the conversation.
    """
    rng = random.Random(hash(source_text) & 0xFFFFFFFF)
    excerpt = _compact_text(source_text, limit=1200)
    key_terms = _extract_key_terms(excerpt)
    # Style-RAG: use random retrieval for a stronger voice anchor (k=5)
    _t0 = time.time()
    with Timer("g_goldset_random_retrieve", labels={"scope": "comments", "k": 5}):
        gold_examples = retrieve_goldset_examples_random(k=5)
    _elapsed_ms = round((time.time() - _t0) * 1000, 2)
    logger.info(
        "[RAG] Retrieved %s random goldset examples for comment in %.2fms",
        len(gold_examples),
        _elapsed_ms,
    )
    tail_angles = _verbalized_tail_sampling(
        topic_abstract=excerpt,
        context=context,
        model=settings.generation_model,
        rag_context=gold_examples,
        max_angles=TAIL_SAMPLING_COUNT,
    )
    tail_block = _format_tail_angles_for_prompt(tail_angles) or "No tail angles surfaced; rely on doctrinal instincts."
    gold_block = _format_gold_examples_for_prompt(gold_examples, limit=5) or "Anchors unavailable. Mirror the contract tone precisely."
    # Emit diagnostics to validate anchor block presence/format during smoke tests
    try:
        diagnostics.info(
            "comment_prompt_gold_block",
            {
                "excerpt_preview": (excerpt or "")[:160],
                "anchors_count": gold_block.count("\n") + (1 if gold_block.strip() else 0),
                "gold_block_preview": gold_block[:240],
            },
        )
    except Exception:
        pass

    use_question = rng.random() < 0.5
    if use_question:
        closing_instruction = "CRITICAL: End with an ultra-intelligent, non-obvious question that 90% of people wouldn't think to ask. This question should spark genuine conversation, not be a generic 'What do you think?'."
    else:
        closing_instruction = "CRITICAL: The comment MUST stand on its own as a complete piece of high-value insight. It should not ask a question."

    key_terms_block = ""
    if key_terms:
        key_terms_block = (
            "\nYou MUST explicitly reference at least one of these terms (quote or clearly paraphrase) to prove we read the post: "
            + ", ".join(key_terms)
            + "."
        )
    hook_line = ""
    if assessment and assessment.hook:
        hook_line = f"\nFocus your reply on this wedge: {assessment.hook}"
    risk_line = ""
    if assessment and assessment.risk:
        risk_line = f"\nAvoid this pitfall: {assessment.risk}"

    prompt_spec = _comment_generation_prompt()
    prompt = prompt_spec.render(
        excerpt=excerpt,
        closing_instruction=closing_instruction,
        banned_words=", ".join(sorted(BANNED_WORDS)),
        key_terms_block=key_terms_block,
        hook_line=hook_line,
        risk_line=risk_line,
        tail_block=tail_block,
        gold_block=gold_block,
    )

    system_message = (
        "You are a fractional COO ghostwriter crafting a conversation-driving reply. "
        "Balance conviction with respect—build on the author's perspective instead of tearing it down. "
        "Respect the style contract, ICP and complementary guidelines strictly.\n\n<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )

    comment = ""
    insight = None
    internal_feedback = ""
    try:
        data = llm.chat_json(
            model=settings.generation_model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.55,
        )
        
        if isinstance(data, dict):
            status = data.get("status")
            if status == "NO_COMMENT":
                reason = data.get("reason", "No specific reason provided.")
                logger.info("Comment skipped by strategic filter: %s", reason)
                raise CommentSkip(reason)
            if status == "COMMENT":
                comment = str(data.get("comment", "")).strip()

        if not comment:
            # Fallback for cases where the model might fail to produce the structured JSON
            raw = llm.chat_text(
                model=settings.generation_model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.65,
            )
            if isinstance(raw, str):
                comment = raw.strip()
    except CommentSkip:
        raise  # Re-raise to be caught by the calling function
    except Exception as e:
        logger.error(f"Error during comment generation: {e}", exc_info=True)
        comment = ""
        insight = None

    if not comment:
        raise StyleRejection("LLM did not generate a valid comment.")

    revised_comment, feedback = _apply_internal_debate(
        "COMMENT",
        comment,
        excerpt,
        context,
        tail_angles,
        settings.validation_model,
    )
    if revised_comment and revised_comment.strip():
        comment = revised_comment.strip()
    if feedback:
        internal_feedback = feedback

    # --- NEW POST-PROCESSING FOR FLEXIBLE COMMENTS ---
    # Goal: Preserve human-like structure (1-3 sentences) while enforcing key constraints.
    comment = comment.strip()

    # 1. Enforce strict 140 character limit
    if len(comment) > 140:
        comment = ensure_under_limit_via_llm(
            comment, settings.validation_model, 140, attempts=4
        )
    if len(comment) > 140:
        raise StyleRejection("Comment exceeds 140 characters after adjustments.")

    # 2. Basic compliance check (allowing commas/conjunctions for a more human feel)
    issues = []
    lower_comment = comment.lower()
    tokens = _WORD_REGEX.findall(lower_comment)
    for banned in BANNED_WORDS:
        if banned in tokens:
            issues.append(f"contains banned word '{banned}'")
    suffix_hits = [
        token
        for token in tokens
        if any(token.endswith(suffix) for suffix in BANNED_SUFFIXES) and len(token) > 2
    ]
    if suffix_hits:
        issues.append(
            "contains forbidden suffix words: " + ", ".join(sorted(set(suffix_hits)))
        )
    if issues:
        raise StyleRejection(f"Comment rejected: {', '.join(issues)}.")

    # 3. Sentence count validation (1-3 sentences allowed)
    sentence_count = _count_sentences(comment)
    if not (1 <= sentence_count <= 3):
        raise StyleRejection(
            f"Comment must have 1-3 sentences, but found {sentence_count}."
        )

    relevance = _validate_comment_relevance(
        excerpt,
        comment,
        context,
        settings.validation_model,
        key_terms=key_terms,
    )
    if not relevance.is_relevant:
        raise StyleRejection(f"Comment discarded as irrelevant: {relevance.reason}")

    audit = "Custom comment validation applied (human format)."

    metadata: Dict[str, object] = {"audit": audit, "source_excerpt": excerpt}
    if insight:
        metadata["insight"] = insight
    if assessment:
        metadata["assessment_reason"] = assessment.reason
        if assessment.hook:
            metadata["assessment_hook"] = assessment.hook
        if assessment.risk:
            metadata["assessment_risk"] = assessment.risk
    if key_terms:
        metadata["key_terms"] = key_terms
    metadata["relevance_reason"] = relevance.reason
    metadata["tail_angles"] = tail_angles
    if gold_examples:
        metadata["gold_examples"] = gold_examples
    metadata["tail_block"] = tail_block
    metadata["style_anchors_block"] = gold_block
    if internal_feedback:
        metadata["internal_feedback"] = internal_feedback
    
    # Since the hook is no longer part of the prompt, we remove it from metadata.
    # metadata["hook"] = hook.name

    return CommentResult(comment=comment.strip(), insight=insight, metadata=metadata)


def _verbalized_tail_sampling(
    topic_abstract: str,
    context: PromptContext,
    model: str,
    rag_context: Optional[List[str]] = None,
    max_angles: int = TAIL_SAMPLING_COUNT,
) -> List[Dict[str, str]]:
    """Generate low-probability hook ideas to prime final drafts."""

    if max_angles <= 0:
        return []

    system_message = (
        "You are a contrarian strategist hunting tail-distribution insights while staying relevant to the COO ICP.\n"
        "Respect the style contract, ICP, and complementary guidelines. Respond ONLY with strict JSON.\n\n"
        "<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n"
        "<ICP>\n"
        + context.icp
        + "\n</ICP>\n"
        "<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )

    rag_section = ""
    if rag_context:
        rag_section = "\nInspirational Context:\n" + "\n".join(f"- {doc}" for doc in rag_context) + "\n"
    prompt_spec = _tail_sampling_prompt()
    user_prompt = prompt_spec.render(
        topic=topic_abstract,
        tail_count=max_angles,
        rag_section=rag_section,
    )

    try:
        resp = llm.chat_json(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.55,
        )
        angles = resp.get("angles") if isinstance(resp, dict) else None
        if not isinstance(angles, list):
            return []
        cleaned: List[Dict[str, str]] = []
        for item in angles[:max_angles]:
            angle = str(item.get("angle", "")).strip()
            if not angle:
                continue
            cleaned.append(
                {
                    "probability": str(item.get("probability", "0.09")).strip() or "0.09",
                    "angle": angle,
                    "mainstream": str(item.get("mainstream", "")).strip(),
                    "rationale": str(item.get("rationale", "")).strip(),
                }
            )
        return cleaned
    except Exception as exc:
        logger.warning("Tail sampling failed: %s", exc)
        return []


def _generate_contrast_analysis(
    topic_abstract: str,
    context: PromptContext,
    model: str,
    rag_context: Optional[List[str]] = None,
) -> Dict[str, str]:
    system_message = (
        "You analyse narratives for a COO-focused audience. Respect the style contract, ICP, and complementary guidelines."
        " Respond ONLY with strict JSON.\n\n<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )

    rag_section = ""
    if rag_context:
        rag_section = "\nInspirational Context:\n" + "\n".join(f"- {doc}" for doc in rag_context) + "\n"
    prompt_spec = _contrast_analysis_prompt()
    user_prompt = prompt_spec.render(
        topic=topic_abstract,
        rag_section=rag_section,
    )


    try:
        resp = llm.chat_json(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.35,
        )
        if isinstance(resp, dict):
            winner = str(resp.get("winner", "")).strip()
            if winner in {"mainstream", "contrarian"}:
                return {
                    "mainstream": str(resp.get("mainstream", "")).strip(),
                    "contrarian": str(resp.get("contrarian", "")).strip(),
                    "winner": winner,
                    "reason": str(resp.get("reason", "")).strip(),
                }
    except Exception as exc:
        logger.warning("Contrast analysis failed: %s", exc)
    return {}


def _count_sentences(text: str) -> int:
    sentences = [s for s in SENTENCE_SPLIT_REGEX.split(text.strip()) if s]
    return len(sentences)


def _format_tail_angles_for_prompt(tail_angles: List[Dict[str, str]]) -> str:
    if not tail_angles:
        return ""
    formatted = []
    for idx, item in enumerate(tail_angles, 1):
        line = f"{idx}. [p={item['probability']}] {item['angle']}"
        if item.get("rationale"):
            line += f" (Why: {item['rationale']})"
        formatted.append(line)
    return "\n".join(formatted)


def _format_gold_examples_for_prompt(examples: List[str], limit: int = 3) -> str:
    if not examples:
        return ""
    lines: List[str] = []
    for idx, example in enumerate(examples[:limit], 1):
        snippet = _compact_text(example, limit=140)
        lines.append(f"{idx}. {snippet}")
    return "\n".join(lines)


def _run_internal_reviews(
    variant_label: str,
    draft: str,
    topic_abstract: str,
    context: PromptContext,
    tail_angles: List[Dict[str, str]],
    model: str,
) -> str:
    if not REVIEWER_PROFILES:
        return ""

    feedback_blocks: List[str] = []
    tail_section = _format_tail_angles_for_prompt(tail_angles)

    for reviewer in REVIEWER_PROFILES:
        system_message = (
            reviewer["role"]
            + "\n\nRespect the COOlogy style contract, ICP, and complementary guidelines. Respond ONLY with strict JSON."
            + "\n\n<STYLE_CONTRACT>\n"
            + context.contract
            + "\n</STYLE_CONTRACT>\n<ICP>\n"
            + context.icp
            + "\n</ICP>\n<FINAL_REVIEW_GUIDELINES>\n"
            + context.final_guidelines
            + "\n</FINAL_REVIEW_GUIDELINES>"
        )

        user_prompt = """
Variant: {variant_label}
Topic: {topic}
Current draft:
---
{draft}
---

{tail_section}

Provide up to 3 bullet critiques focused on: {focus}
Format strictly as {{"bullets": ["..."]}}. Bullets ≤ 140 characters.
""".format(
            variant_label=variant_label,
            topic=topic_abstract,
            draft=draft,
            tail_section=("Tail angles to respect:\n" + tail_section) if tail_section else "",
            focus=reviewer["focus"],
        )

        try:
            resp = llm.chat_json(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
            )
        except Exception as exc:
            logger.warning("Internal review (%s) failed: %s", reviewer["name"], exc)
            continue

        bullets = resp.get("bullets") if isinstance(resp, dict) else None
        if not isinstance(bullets, list):
            continue
        cleaned = [str(b).strip() for b in bullets if str(b).strip()]
        if cleaned:
            feedback_blocks.append(f"{reviewer['name']}: " + " | ".join(cleaned))

    return "\n".join(feedback_blocks)


def _revise_with_reviews(
    variant_label: str,
    draft: str,
    feedback: str,
    topic_abstract: str,
    context: PromptContext,
    tail_angles: List[Dict[str, str]],
    model: str,
) -> Optional[str]:
    if not feedback.strip():
        return None

    tail_section = _format_tail_angles_for_prompt(tail_angles)
    variant_rules = {
        "A": "Stay under 280 characters. One punchy paragraph or 1–2 short sentences.",
        "B": "Exactly two sentences. No filler. ≤280 characters.",
        "C": "Exactly one sentence. Ruthless. ≤280 characters.",
        "COMMENT": "≤140 characters. One tight paragraph. Tie back to the author's core term and advance the conversation.",
    }
    variant_instruction = variant_rules.get(variant_label.upper(), "Stay under 280 characters.")

    user_prompt = """
Variant {variant_label} must be rewritten using the internal feedback.

Topic: {topic}
Current draft:
---
{draft}
---

Feedback received:
{feedback}

{tail_block}

Rewrite constraints:
- {variant_instruction}
- Maintain COOlogy style contract, ICP, and complementary guidelines.
- Zero hedging, no corporate tone, keep it human and direct.
- Return ONLY the revised text (no quotes or comments).
""".format(
        variant_label=variant_label,
        topic=topic_abstract,
        draft=draft,
        feedback=feedback,
        tail_block=("Tail angles to honor:\n" + tail_section) if tail_section else "",
        variant_instruction=variant_instruction,
    )

    system_message = (
        "You are a world-class ghostwriter revising copy after an internal debate."
        " Respect the style contract, ICP, and complementary guidelines strictly."
        "\n\n<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )

    try:
        revised = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
        if isinstance(revised, str):
            return revised.strip()
    except Exception as exc:
        logger.warning("Could not apply internal review (%s): %s", variant_label, exc)
    return None


def _apply_internal_debate(
    variant_label: str,
    draft: str,
    topic_abstract: str,
    context: PromptContext,
    tail_angles: List[Dict[str, str]],
    model: str,
) -> Tuple[str, str]:
    feedback = _run_internal_reviews(variant_label, draft, topic_abstract, context, tail_angles, model)
    if not feedback:
        return draft, ""
    logger.info("Internal feedback for variant %s:\n%s", variant_label, feedback)
    revised = _revise_with_reviews(variant_label, draft, feedback, topic_abstract, context, tail_angles, model)
    if revised and revised.strip():
        return revised.strip(), feedback
    return draft, feedback


def _summarise_feedback(feedback: str) -> Optional[str]:
    if not feedback:
        return None
    first_line = feedback.split("\n", 1)[0]
    return first_line.strip() if first_line.strip() else None


def _build_reasoning_summary(
    tail_angles: List[Dict[str, str]],
    contrast: Dict[str, str],
    feedback_map: Dict[str, str],
) -> str:
    lines = ["🧠 Internal reasoning"]
    if tail_angles:
        top_angles = [f"[p={item['probability']}] {item['angle']}" for item in tail_angles[:3]]
        lines.append("• Tail angles: " + " / ".join(top_angles))
    if contrast and contrast.get("winner"):
        winner = contrast["winner"].capitalize()
        reason = contrast.get("reason") or contrast.get(winner.lower(), "")
        summary = f"• Contrast winner: {winner} — {reason[:120]}" if reason else f"• Contrast winner: {winner}"
        lines.append(summary)
    for label in ("A", "B", "C"):
        fb = _summarise_feedback(feedback_map.get(label, ""))
        if fb:
            lines.append(f"• Reviewer {label}: {fb}")
    return "\n".join(lines)


def _enforce_sentence_count(
    text: str,
    desired_count: int,
    context: PromptContext,
    model: str,
) -> str:
    instruction = (
        f"Rewrite the text to EXACTLY {desired_count} sentence{'s' if desired_count != 1 else ''}. "
        "Keep the persona, ICP, and tone contract intact. No bullets, no numbering, no emojis."
    )
    try:
        rewritten = llm.chat_text(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a world-class ghostwriter who must obey the style contract, ICP, and final review guidelines.\n\n"
                        "<STYLE_CONTRACT>\n"
                        + context.contract
                        + "\n</STYLE_CONTRACT>\n\n"
                        "<ICP>\n"
                        + context.icp
                        + "\n</ICP>\n\n"
                        "<FINAL_REVIEW_GUIDELINES>\n"
                        + context.final_guidelines
                        + "\n</FINAL_REVIEW_GUIDELINES>"
                    ),
                },
                {
                    "role": "user",
                    "content": instruction + "\n\nTEXT:\n" + text,
                },
            ],
            temperature=0.4,
        )
        return rewritten.strip() if isinstance(rewritten, str) and rewritten.strip() else text
    except Exception as exc:
        logger.warning("Could not adjust sentence count: %s", exc)
        return text
