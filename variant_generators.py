import os
import random
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from llm_fallback import llm
from logger_config import logger
from prompt_context import PromptContext
from style_guard import StyleRejection, improve_style


@dataclass(frozen=True)
class GenerationSettings:
    generation_model: str
    validation_model: str


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
) -> Tuple[str, str]:
    tail_angles = _verbalized_tail_sampling(topic_abstract, context, settings.generation_model)
    tail_prompt = ""
    if tail_angles:
        logger.info("Tail sampling generated %s hook angles for A/B.", len(tail_angles))
        formatted_lines: List[str] = []
        for idx, item in enumerate(tail_angles, 1):
            parts = [f"{idx}. [p={item['probability']}] {item['angle']}"]
            if item.get("mainstream"):
                parts.append(f"Mainstream: {item['mainstream']}")
            if item.get("rationale"):
                parts.append(f"Why it matters: {item['rationale']}")
            formatted_lines.append(" | ".join(parts))
        tail_prompt = (
            "\n\nTail-sampled contrarian angles (use them as the spine of your outputs):\n"
            + "\n".join(formatted_lines)
            + "\n- Variant A must lean into the boldest, most contrarian angle above.\n"
            + "- Variant B must use a different angle, highlighting contrast or tension."
        )

    prompt = (
        _build_ab_prompt(topic_abstract, context)
        + "\n\nVariant A: You may be fully creative as long as the persona, ICP, and tone contract are obeyed."
        + "\nVariant B: Output exactly two sentences (no more, no fewer), each punchy and aligned with the persona."
        + tail_prompt
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

    if _count_sentences(draft_b) != 2:
        draft_b = _enforce_sentence_count(draft_b, 2, context, settings.validation_model)

    if len(draft_a) > 280:
        draft_a = ensure_under_limit_via_llm(draft_a, settings.validation_model, 280, attempts=4)
    if len(draft_b) > 280:
        draft_b = ensure_under_limit_via_llm(draft_b, settings.validation_model, 280, attempts=4)

    if len(draft_a) > 280 or len(draft_b) > 280:
        raise StyleRejection("Alguna alternativa excede los 280 caracteres tras reescritura.")

    return draft_a.strip(), draft_b.strip()


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

    if _count_sentences(c2) != 1:
        c2 = _enforce_sentence_count(c2, 1, context, settings.validation_model)

    if len(c2) > 280:
        c2 = ensure_under_limit_via_llm(c2, settings.validation_model, 280, attempts=4)

    if len(c2) > 280:
        raise StyleRejection("Variant C exceeds 280 characters tras reescritura.")

    return c2.strip(), cat_name


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


def _count_sentences(text: str) -> int:
    sentences = [s for s in SENTENCE_SPLIT_REGEX.split(text.strip()) if s]
    return len(sentences)


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
