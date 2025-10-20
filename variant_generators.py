import os
import random
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from llm_fallback import llm
from logger_config import logger
from prompt_context import PromptContext
from style_guard import StyleRejection, improve_style


@dataclass(frozen=True)
class GenerationSettings:
    generation_model: str
    validation_model: str


@dataclass
class ABGenerationResult:
    draft_a: str
    draft_b: str
    reasoning_summary: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class VariantCResult:
    draft: str
    category: str
    reasoning_summary: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class CommentResult:
    comment: str
    insight: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)


DEFAULT_POST_CATEGORIES: List[Dict[str, str]] = [
    {
        "key": "contrast_statement",
        "name": "Declaración de Contraste",
        "pattern": (
            "Present two opposing ideas to create an instant reveal. Invalidate a common approach (Don't do X), then "
            "present a stronger alternative (Do Y) — position Y as the obvious solution."
        ),
    },
    {
        "key": "perspective_reframe",
        "name": "Reencuadre de Perspectiva",
        "pattern": (
            "Start with a universal truth the reader recognizes. Introduce a twist that reframes it — turn a negative "
            "(struggle) into a necessary element for a positive outcome (victory)."
        ),
    },
    {
        "key": "friction_reduction",
        "name": "Argumento de Reducción de Fricción",
        "pattern": (
            "Directly address analysis paralysis or fear to start. Break an intimidating goal into an absurdly small, "
            "manageable first step that motivates immediate action."
        ),
    },
    {
        "key": "identity_redefinition",
        "name": "Redefinición de Identidad",
        "pattern": (
            "Dismantle a limiting label (e.g., 'I'm not a salesperson'). Replace it with a simpler, authentic requirement "
            "that feels attainable and aligned with the reader's identity."
        ),
    },
    {
        "key": "parallel_contrast_aphorism",
        "name": "Aforismo de Contraste Paralelo",
        "pattern": (
            "Use parallel, aphoristic contrast to juxtapose two ideas. Start from a familiar saying (If A is B), then "
            "present a surprising counterpart (then C is D). Keep symmetry and punch."
        ),
    },
    {
        "key": "demonstrative_principle",
        "name": "Principio Demostrativo",
        "pattern": (
            "Teach a copywriting rule by showing. Contrast a 'bad' version (feature) with a 'good' version (benefit), "
            "then conclude with the principle demonstrated."
        ),
    },
    {
        "key": "counterintuitive_principle",
        "name": "Principio Contraintuitivo",
        "pattern": (
            "State a counterintuitive rule as a near-universal law that challenges a popular belief. Push the reader to "
            "adopt a more effective method by reframing the goal."
        ),
    },
    {
        "key": "process_promise",
        "name": "Promesa de Proceso",
        "pattern": (
            "Validate the reader's frustration: change isn't instant. Offer a future promise — tiny, consistent effort adds up "
            "to a total transformation. Encourage patience and trust in the process."
        ),
    },
    {
        "key": "common_villain_exposure",
        "name": "Exposición del Villano Común",
        "pattern": (
            "Recreate a recognizable negative scenario the reader detests. Expose and criticize the shared villain to build "
            "instant trust, positioning the writer as an ally."
        ),
    },
    {
        "key": "hidden_benefits_reveal",
        "name": "Revelación de Beneficios Ocultos",
        "pattern": (
            "Start with a promise to reveal the non-obvious value of an action. Use a short list to enumerate specific, "
            "unexpected benefits — make the abstract tangible."
        ),
    },
    {
        "key": "values_manifesto",
        "name": "Manifiesto de Valores",
        "pattern": (
            "Redefine a popular idea with a value hierarchy in a compact list. Use A > B comparisons to prioritize deep "
            "principles over superficial alternatives."
        ),
    },
    {
        "key": "delayed_gratification_formula",
        "name": "Fórmula de Gratificación Aplazada",
        "pattern": (
            "State a direct cause-effect between present sacrifice and future reward. Structure like: Do the hard thing today, "
            "get the desired outcome tomorrow. Motivate disciplined action."
        ),
    },
    {
        "key": "excuse_invalidation",
        "name": "Invalidación de Excusa",
        "pattern": (
            "Identify a common external excuse (the blamed villain). Then absolve it and redirect responsibility to an internal "
            "action or inaction, empowering the reader."
        ),
    },
    {
        "key": "revealing_definition",
        "name": "Definición Reveladora",
        "pattern": (
            "Redefine a known concept with a sharp metaphor that reveals its overlooked essence, raising its perceived value."
        ),
    },
    {
        "key": "fundamental_maxim",
        "name": "Máxima Fundamental",
        "pattern": (
            "Present a core principle as a non-negotiable rule of the domain. Reset priorities by exposing the true hierarchy."
        ),
    },
    {
        "key": "paradox_statement",
        "name": "Declaración Paradójica",
        "pattern": (
            "Drop a claim that sounds self-contradictory to break the reader's pattern. Hook curiosity, then resolve the paradox "
            "with a practical reason that proves the rule."
        ),
    },
]


POST_CATEGORIES_PATH = os.getenv(
    "POST_CATEGORIES_PATH",
    os.path.join(os.path.abspath(os.path.dirname(__file__)), "config", "post_categories.json"),
)

BULLET_CATEGORIES = {
    "hidden_benefits_reveal",
    "values_manifesto",
    "demonstrative_principle",
    "friction_reduction",
}

_CACHED_POST_CATEGORIES: List[Dict[str, str]] = []

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
            "Point the exact spots where tone drifts, verbs weaken, o el contrato/ICP se violan. Ofrece una corrección directa."
        ),
    },
    {
        "name": "Clarity Reviewer",
        "role": (
            "You are ruthless about clarity and specificity. If algo suena abstracto, exiges un ejemplo concreto o métrica."
        ),
        "focus": (
            "Resalta afirmaciones vagas, métricas faltantes o stakes difusos. Sugiere qué detalle tangible lo haría innegable."
        ),
    },
]

SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")


def load_post_categories() -> List[Dict[str, str]]:
    global _CACHED_POST_CATEGORIES
    if _CACHED_POST_CATEGORIES:
        return _CACHED_POST_CATEGORIES
    try:
        if POST_CATEGORIES_PATH and os.path.exists(POST_CATEGORIES_PATH):
            with open(POST_CATEGORIES_PATH, "r", encoding="utf-8") as f:
                data = f.read().strip()
                if data:
                    import json

                    parsed = json.loads(data)
                    valid = []
                    for item in parsed:
                        if not isinstance(item, dict):
                            continue
                        key = (item.get("key") or "").strip()
                        name = (item.get("name") or "").strip()
                        pattern = (item.get("pattern") or "").strip()
                        structure = (item.get("structure") or "").strip()
                        why = (item.get("why") or "").strip()
                        if key and name and pattern:
                            valid.append(
                                {
                                    "key": key,
                                    "name": name,
                                    "pattern": pattern,
                                    "structure": structure,
                                    "why": why,
                                }
                            )
                    if valid:
                        _CACHED_POST_CATEGORIES = valid
                        logger.info(f"Loaded {len(valid)} post categories from JSON.")
                        return _CACHED_POST_CATEGORIES
    except Exception as exc:
        logger.warning(f"Failed to load post categories from '{POST_CATEGORIES_PATH}': {exc}")
    _CACHED_POST_CATEGORIES = DEFAULT_POST_CATEGORIES
    return _CACHED_POST_CATEGORIES


def pick_random_post_category() -> Dict[str, str]:
    return random.choice(load_post_categories())


def ensure_under_limit_via_llm(
    text: str,
    model: str,
    limit: int = 280,
    attempts: int = 4,
) -> str:
    attempt = 0
    best = text
    while attempt < attempts:
        attempt += 1
        prompt = (
            f"Rewrite the text so the TOTAL characters are <= {limit}. Preserve meaning and readability. "
            f"Do NOT add quotes, emojis or hashtags. Prefer short words and compact phrasing. Return ONLY JSON: "
            f'{{"text": "<final text under {limit} chars>"}}. Text must be <= {limit} characters.\n\n'
            f"TEXT: {best}"
        )
        try:
            data = llm.chat_json(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a ruthless editor returning strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.25,
            )
            candidate = (data or {}).get("text") if isinstance(data, dict) else None
            if isinstance(candidate, str) and candidate.strip():
                candidate = candidate.strip()
                if len(candidate) <= limit:
                    return candidate
                best = candidate
        except Exception:
            continue
    return best


def _compact_text(text: str, limit: int = 1200) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return cut + "…"


def _ensure_question_at_end(text: str, model: str, context: PromptContext) -> str:
    if "?" in text:
        return text
    prompt = (
        "Rewrite the comment so it still says the same thing but ends with a sharp, specific question that invites a reply. "
        "Keep it under 280 characters, no emojis or hashtags."
    )
    system_message = (
        "You are a world-class ghostwriter tightening a short social media reply.\n\n<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )
    try:
        revised = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": f"{prompt}\n\nCOMMENT:\n{text}"},
            ],
            temperature=0.4,
        )
        if isinstance(revised, str) and revised.strip():
            return revised.strip()
    except Exception:
        pass
    return text


def _build_ab_prompt(topic_abstract: str, context: PromptContext) -> str:
    return f"""
            You are a ghostwriter. Your task is to write TWO distinct alternatives for a tweet based on the topic below. Strictly follow the provided contract.

            **Contract for style and tone:**
            {context.contract}
            ---
            **Complementary polish guardrails (do not override the contract/ICP):**
            {context.final_guidelines}
            ---
            **Topic:** {topic_abstract}

            **Style Amplifier (must do):**
            - NYC bar voice: smart, direct, slightly impatient; zero corporate tone.
            - Open with a punchy first line (no 'Most people…', no 'Counter‑intuitive truth:').
            - Include one concrete image or tactical detail (micro‑visual) to make it feel real.
            - No hedging or qualifiers (no seems/maybe/might). Strong verbs only.
            - 2–4 short paragraphs separated by a blank line. English only. No quotes around the output. No emojis/hashtags.
            - A and B MUST use different opening patterns (e.g., question vs. bold statement vs. vivid image).

            **CRITICAL OUTPUT REQUIREMENTS:**
            - Provide two high-quality, distinct alternatives in English.
            - Both alternatives MUST be under 280 characters.
            - The output will be automatically structured, so do not add any labels like [EN - A] or [EN - B].
            """


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


def _refine_single_tweet_style(raw_text: str, model: str, context: PromptContext) -> str:
    prompt = (
        "Rewrite the text to hit a sharper, NYC bar voice — smart, direct, a bit impatient — without losing the core insight.\n"
        "Do NOT add emojis or hashtags. Do NOT use Spanish.\n\n"
        "Amplifiers (must do):\n"
        "- Start with a punchy opener (no 'Most people…', no hedging).\n"
        "- Include one concrete image or tactical detail (micro‑visual).\n"
        "- Cut filler, adverbs, qualifiers (seems, maybe, might).\n"
        "- Strong verbs, short sentences, no corporate wording.\n"
        "- 2–4 short paragraphs separated by a blank line.\n"
        "- Keep under 280 characters.\n\n"
        f"RAW TEXT: --- {raw_text} ---"
    )
    system_message = (
        "You are a world-class ghostwriter rewriting text into a specific style. "
        "Follow the style contract exactly. Keep it concise and punchy.\n\n"
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
    try:
        text = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
        )
        return text
    except Exception:
        return raw_text


def _refine_single_tweet_style_flexible(raw_text: str, model: str, context: PromptContext) -> str:
    prompt = (
        "Rewrite the text to hit a sharper, NYC bar voice — smart, direct, a bit impatient — without losing the core insight.\n"
        "Do NOT add emojis or hashtags. Do NOT use Spanish.\n\n"
        "Amplifiers (must do):\n"
        "- Start with a punchy opener (no hedging).\n"
        "- Include one concrete image or tactical detail (micro‑visual).\n"
        "- Cut filler, adverbs, qualifiers (seems, maybe, might).\n"
        "- Strong verbs, short sentences, no corporate wording.\n\n"
        "Structure (flexible for C):\n"
        "- You MAY output a single hard‑hitting sentence.\n"
        "- Or 1–3 short sentences, same paragraph.\n"
        "- Or up to 2 very short paragraphs.\n"
        "- Keep it under 280 characters.\n\n"
        f"RAW TEXT: --- {raw_text} ---"
    )
    system_message = (
        "You are a world-class ghostwriter rewriting text into a specific style. "
        "Follow the style contract exactly, EXCEPT paragraph-count rules are explicitly overridden for this variant.\n\n"
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
    try:
        text = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
        )
        return text
    except Exception:
        return raw_text


def generate_variant_ab_pair(
    topic_abstract: str,
    context: PromptContext,
    settings: GenerationSettings,
) -> ABGenerationResult:
    tail_angles = _verbalized_tail_sampling(topic_abstract, context, settings.generation_model)
    tail_prompt = ""
    if tail_angles:
        logger.info("Tail sampling generated %s hook angles for A/B.", len(tail_angles))
        formatted_lines = []
        for idx, item in enumerate(tail_angles, 1):
            line = f"{idx}. [p={item['probability']}] {item['angle']}"
            if item.get("rationale"):
                line += f" (Why: {item['rationale']})"
            formatted_lines.append(line)
        tail_prompt = (
            "\n\nTail-sampled contrarian angles (use them as the spine of your outputs):\n"
            + "\n".join(formatted_lines)
            + "\n- Variant A must lean into the boldest angle above.\n"
            + "- Variant B must use a different angle, highlighting contrast or tension."
        )

    contrast = _generate_contrast_analysis(topic_abstract, context, settings.generation_model)
    contrast_prompt = ""
    if contrast:
        contrast_prompt = (
            "\n\nContrast analysis (mainstream vs contrarian):\n"
            f"- Mainstream: {contrast.get('mainstream', '')}\n"
            f"- Contrarian: {contrast.get('contrarian', '')}\n"
            f"- Winner: {contrast.get('winner', '')} (reason: {contrast.get('reason', '')})\n"
            "Anchor both variants in the winning perspective."
        )

    prompt = (
        _build_ab_prompt(topic_abstract, context)
        + "\n\nVariant A: You may be fully creative as long as the persona, ICP, and tone contract are obeyed."
        + "\nVariant B: Output exactly two sentences (no more, no fewer), each punchy and aligned with the persona."
        + tail_prompt
        + contrast_prompt
    )

    logger.info("Generando variantes A y B via LLM (JSON preferred).")
    draft_a = ""
    draft_b = ""
    try:
        resp = llm.chat_json(
            model=settings.generation_model,
            messages=[
                {"role": "system", "content": _build_system_message(context)},
                {
                    "role": "user",
                    "content": (
                        prompt
                        + '\n\nOutput format (strict JSON): {\n  "draft_a": "...",\n  "draft_b": "..." \n}'
                    ),
                },
            ],
            temperature=0.65,
        )
        if isinstance(resp, dict):
            draft_a = str(resp.get("draft_a", "")).strip()
            draft_b = str(resp.get("draft_b", "")).strip()
    except Exception as e_json:
        logger.warning(f"JSON generation failed, fallback to text parse: {e_json}")

    if not draft_a or not draft_b:
        logger.info("Falling back to delimiter-based response for variants A/B.")
        plain = llm.chat_text(
            model=settings.generation_model,
            messages=[
                {"role": "system", "content": "You are a world-class ghostwriter creating two tweet drafts."},
                {
                    "role": "user",
                    "content": (
                        prompt
                        + "\n\nReturn two alternatives under 280 chars each."
                        + " Use the exact delimiter on a single line between them: ---"
                    ),
                },
            ],
            temperature=0.65,
        )
        if isinstance(plain, str) and plain.strip():
            if "\n---\n" in plain:
                part_a, part_b = plain.split("\n---\n", 1)
                draft_a = part_a.strip()
                draft_b = part_b.strip()
            else:
                parts = [p.strip() for p in plain.split("\n\n") if p.strip()]
                if len(parts) >= 2:
                    draft_a, draft_b = parts[0], parts[1]
                else:
                    draft_a = plain.strip()
                    draft_b = plain.strip()[: max(0, len(plain.strip()) - 1)]

    if not draft_a or not draft_b:
        raise StyleRejection("LLM failed to produce both variants.")

    draft_a = _refine_single_tweet_style(draft_a, settings.validation_model, context)
    draft_b = _refine_single_tweet_style(draft_b, settings.validation_model, context)

    improved_a, audit_a = improve_style(draft_a, context.contract)
    if improved_a and improved_a != draft_a:
        logger.info(f"Auditoría A: se aplicó revisión de estilo. Detalle: {audit_a}")
        draft_a = improved_a
    else:
        logger.info(f"Auditoría A: sin cambios. Detalle: {audit_a}")

    improved_b, audit_b = improve_style(draft_b, context.contract)
    if improved_b and improved_b != draft_b:
        logger.info(f"Auditoría B: se aplicó revisión de estilo. Detalle: {audit_b}")
        draft_b = improved_b
    else:
        logger.info(f"Auditoría B: sin cambios. Detalle: {audit_b}")

    draft_a, feedback_a = _apply_internal_debate("A", draft_a, topic_abstract, context, tail_angles, settings.validation_model)
    draft_b, feedback_b = _apply_internal_debate("B", draft_b, topic_abstract, context, tail_angles, settings.validation_model)

    draft_a = _refine_single_tweet_style(draft_a, settings.validation_model, context)
    post_improved_a, post_audit_a = improve_style(draft_a, context.contract)
    if post_improved_a and post_improved_a != draft_a:
        logger.info(f"Auditoría post-debate A: se aplicó revisión de estilo. Detalle: {post_audit_a}")
        draft_a = post_improved_a
    elif post_audit_a:
        logger.info(f"Auditoría post-debate A: sin cambios. Detalle: {post_audit_a}")

    draft_b = _refine_single_tweet_style(draft_b, settings.validation_model, context)
    post_improved_b, post_audit_b = improve_style(draft_b, context.contract)
    if post_improved_b and post_improved_b != draft_b:
        logger.info(f"Auditoría post-debate B: se aplicó revisión de estilo. Detalle: {post_audit_b}")
        draft_b = post_improved_b
    elif post_audit_b:
        logger.info(f"Auditoría post-debate B: sin cambios. Detalle: {post_audit_b}")

    if _count_sentences(draft_b) != 2:
        draft_b = _enforce_sentence_count(draft_b, 2, context, settings.validation_model)

    if len(draft_a) > 280:
        draft_a = ensure_under_limit_via_llm(draft_a, settings.validation_model, 280, attempts=4)
    if len(draft_b) > 280:
        draft_b = ensure_under_limit_via_llm(draft_b, settings.validation_model, 280, attempts=4)

    if len(draft_a) > 280 or len(draft_b) > 280:
        raise StyleRejection("Alguna alternativa excede los 280 caracteres tras reescritura.")

    feedback_map = {"A": feedback_a, "B": feedback_b}
    reasoning_summary = _build_reasoning_summary(tail_angles, contrast, feedback_map)
    metadata = {
        "tail_angles": tail_angles,
        "contrast": contrast,
        "feedback": feedback_map,
    }

    return ABGenerationResult(draft_a.strip(), draft_b.strip(), reasoning_summary, metadata)


def generate_variant_c(
    topic_abstract: str,
    context: PromptContext,
    settings: GenerationSettings,
) -> Tuple[str, str]:
    category = pick_random_post_category()
    cat_name = category["name"]
    cat_desc = category["pattern"]
    cat_struct = (category.get("structure") or "").strip()
    cat_why = (category.get("why") or "").strip()

    use_bullets = category.get("key") in BULLET_CATEGORIES
    prompt = f"""
**Audience:** Remember you are talking to a friend who fits the Ideal Customer Profile (ICP) below. Your tone should be like giving direct, valuable advice to them.

**Core Task:** Your goal is to follow the *spirit* and *rationale* of the category. The 'why' and 'pattern' are more important than a rigid adherence to the 'structure'. The output should make the reader feel a certain way or see *themselves* differently, as described in the category's rationale.

**Category Details:**
- Category: {cat_name}
- Pattern: {cat_desc}
- Structure: {('Structure template: ' + cat_struct) if cat_struct else ''}
- Rationale: {('Technique rationale: ' + cat_why) if cat_why else ''}

**Style and Output Rules:**
- **CRITICAL Constraint:** You MUST output a single sentence that hits like a slap in the face for the ICP. No hedging, no metaphors, no analogies.
- Voice: NYC bar voice: smart, direct, slightly impatient; zero corporate tone.
- Structure: exactly one sentence, ruthless and unavoidable.
- Format: No emojis or hashtags. No quotes around the output. English only.
- Length: Keep under 280 characters (hard requirement).

**Topic:** {topic_abstract}
"""

    tail_angles = _verbalized_tail_sampling(topic_abstract, context, settings.generation_model, max_angles=2)
    if tail_angles:
        formatted = []
        for idx, item in enumerate(tail_angles, 1):
            line = f"{idx}. [p={item['probability']}] {item['angle']}"
            if item.get("rationale"):
                line += f" (Why: {item['rationale']})"
            formatted.append(line)
        prompt += (
            "\nTail-sampled spikes to infuse (choose one and make it unavoidable):\n"
            + "\n".join(formatted)
        )

    contrast = _generate_contrast_analysis(topic_abstract, context, settings.generation_model)
    if contrast:
        prompt += (
            "\nContrast insight:\n"
            f"- Mainstream: {contrast.get('mainstream', '')}\n"
            f"- Contrarian: {contrast.get('contrarian', '')}\n"
            f"- Winner: {contrast.get('winner', '')} (reason: {contrast.get('reason', '')})\n"
            "Fuse the winning narrative into the single sentence."
        )

    if use_bullets:
        prompt += "\n**Hint:** Short bullet list is acceptable for this pattern.\n"

    system_message = (
        "You are a world-class ghostwriter. Obey the following style contract strictly.\n\n<STYLE_CONTRACT>\n"
        + context.contract
        + "\n</STYLE_CONTRACT>\n\n"
        "Audience ICP:\n<ICP>\n"
        + context.icp
        + "\n</ICP>\n\n"
        "Complementary polish rules (do not override the contract/ICP):\n<FINAL_REVIEW_GUIDELINES>\n"
        + context.final_guidelines
        + "\n</FINAL_REVIEW_GUIDELINES>"
    )

    raw_c = llm.chat_text(
        model=settings.generation_model,
        messages=[
            {"role": "system", "content": system_message},
            {
                "role": "user",
                "content": (
                    prompt
                    + "\n\nOverride for C: Ignore any paragraph-count constraints from the style contract. "
                    "You may output a single strong sentence, 1–3 short sentences, or up to 2 very short paragraphs."
                ),
            },
        ],
        temperature=0.75,
    )

    c1 = _refine_single_tweet_style_flexible(raw_c, settings.validation_model, context)
    improved_c, _ = improve_style(c1, context.contract)
    c2 = improved_c or c1

    c2, feedback_c = _apply_internal_debate("C", c2, topic_abstract, context, tail_angles, settings.validation_model)

    if _count_sentences(c2) != 1:
        c2 = _enforce_sentence_count(c2, 1, context, settings.validation_model)

    if len(c2) > 280:
        c2 = ensure_under_limit_via_llm(c2, settings.validation_model, 280, attempts=4)

    if len(c2) > 280:
        raise StyleRejection("Variant C exceeds 280 characters tras reescritura.")

    feedback_map = {"C": feedback_c}
    reasoning_summary = _build_reasoning_summary(tail_angles, contrast, feedback_map)
    metadata = {
        "tail_angles": tail_angles,
        "contrast": contrast,
        "feedback": feedback_map,
    }

    return VariantCResult(c2.strip(), cat_name, reasoning_summary, metadata)


def generate_comment_reply(
    source_text: str,
    context: PromptContext,
    settings: GenerationSettings,
) -> CommentResult:
    """
    Generate a single conversational reply/comment anchored on the provided source text.
    The output keeps the ICP voice and ends with an invitation to continue the conversation.
    """

    excerpt = _compact_text(source_text, limit=1200)
    prompt = f"""
We are replying to the following post. Draft ONE short comment (<=280 characters) that proves we actually read it and invites a response.

POST (raw):
\"\"\"{excerpt}\"\"\"

Rules:
- Lead with a concrete tension or observation lifted from the post (quote/paraphrase).
- Add one tactic or lens tied to operator/COO pains (ICP).
- End with a pointed question or next-step challenge to spark conversation.
- Voice: NYC bar sharp, no fluff, no emojis/hashtags, English only.
- Two sentences maximum. Keep it human and direct.
"""

    system_message = (
        "You are a fractional COO ghostwriter crafting a conversation-driving reply. "
        "Respect the style contract, ICP and complementary guidelines strictly.\n\n<STYLE_CONTRACT>\n"
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
            temperature=0.55,
        )
        comment = ""
        insight = None
        if isinstance(data, dict):
            comment = str(data.get("comment") or data.get("reply") or "").strip()
            insight_raw = data.get("insight") or data.get("focus")
            if isinstance(insight_raw, str):
                insight = insight_raw.strip()[:160]
        else:
            comment = ""
    except Exception:
        comment = ""
        insight = None

    if not comment:
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

    if not comment:
        raise StyleRejection("LLM no generó un comentario válido.")

    comment = _refine_single_tweet_style_flexible(comment, settings.validation_model, context)
    improved, audit = improve_style(comment, context.contract)
    if improved and improved != comment:
        logger.info("Auditoría comentario: se reforzó el estilo. %s", audit)
        comment = improved
    elif audit:
        logger.info("Auditoría comentario: sin cambios. %s", audit)

    comment = _ensure_question_at_end(comment, settings.validation_model, context)
    if len(comment) > 280:
        comment = ensure_under_limit_via_llm(comment, settings.validation_model, 280, attempts=4)

    if len(comment) > 280:
        raise StyleRejection("Comentario excede los 280 caracteres tras ajustes.")

    metadata: Dict[str, object] = {"audit": audit, "source_excerpt": excerpt}
    if insight:
        metadata["insight"] = insight

    return CommentResult(comment=comment.strip(), insight=insight, metadata=metadata)


def _verbalized_tail_sampling(
    topic_abstract: str,
    context: PromptContext,
    model: str,
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

    user_prompt = f"""
We are drafting content for a fractional COO persona. Use verbalized sampling to explore {max_angles} low-probability hooks (p < 0.15) about:

TOPIC: {topic_abstract}

For each hook:
1. Identify the mainstream narrative you're challenging.
2. Summarize the contrarian/orthogonal insight (≤ 2 sentences).
3. Provide a probability string like "0.08" (must be < 0.15).
4. Explain briefly why this tail angle matters for an overwhelmed day 1–year 1 solopreneur.

Return JSON like:
{{
  "angles": [
    {{
      "probability": "0.09",
      "mainstream": "...",
      "angle": "...",
      "rationale": "..."
    }}
  ]
}}

Keep each field ≤ 180 characters."""

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

    user_prompt = f"""
Topic: {topic_abstract}

1. Describe the mainstream narrative most creators repeat about este tópico (≤160 chars).
2. Describe a contrarian/orthogonal narrative that un COO fractional sí debería empujar (≤160 chars).
3. Decide cuál narrativa golpea más duro al ICP y explica por qué en ≤160 chars.

Return strict JSON:
{{
  "mainstream": "...",
  "contrarian": "...",
  "winner": "mainstream|contrarian",
  "reason": "..."
}}
"""

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
- Maintain COOlogy style contract, ICP, y pautas complementarias.
- Cero hedging, sin tono corporativo, manténlo humano y directo.
- Devuelve SOLO el texto revisado (sin comillas ni comentarios).
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
        logger.warning("No se pudo aplicar revisión interna (%s): %s", variant_label, exc)
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
        logger.warning("No se pudo ajustar el conteo de oraciones: %s", exc)
        return text
