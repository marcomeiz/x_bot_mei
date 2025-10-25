import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from llm_fallback import llm
from logger_config import logger
from prompt_context import PromptContext
from style_guard import StyleRejection, improve_style
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


STOPWORDS = {
    "the",
    "and",
    "that",
    "with",
    "this",
    "from",
    "they",
    "have",
    "will",
    "your",
    "their",
    "about",
    "there",
    "what",
    "when",
    "where",
    "while",
    "would",
    "could",
    "should",
    "might",
    "into",
    "over",
    "under",
    "only",
    "just",
    "been",
    "being",
    "once",
    "also",
    "more",
    "less",
    "than",
    "then",
    "such",
    "even",
    "some",
    "most",
    "much",
    "very",
    "like",
    "felt",
    "them",
    "ours",
    "ourselves",
    "yours",
    "yourself",
    "myself",
    "hers",
    "herself",
    "himself",
    "itself",
    "each",
    "because",
    "which",
    "into",
    "onto",
    "among",
    "after",
    "before",
    "again",
    "between",
    "across",
    "around",
    "through",
    "every",
}


def _pick_hooks_for_variants(rng: random.Random, count: int) -> List[int]:
    """Return indices into HOOK_GUIDELINES ensuring variety."""
    population = list(range(len(HOOK_GUIDELINES)))
    if count >= len(population):
        rng.shuffle(population)
        return population[:count]
    return rng.sample(population, count)


def _build_shared_rules() -> str:
    return (
        "Common guardrails for every variant:\n"
        f"{hook_menu()}\n"
        "- First sentence MUST deploy the chosen hook in ≤8 words (unless the format overrides it).\n"
        f"{visual_anchor_prompt()}"
        f"{words_blocklist_prompt()}"
        f"{comma_guard_prompt()}"
        f"{conjunction_guard_prompt()}"
        "- Zero emojis, hashtags, or Spanish. English only.\n"
        "- Everything under 280 characters.\n"
        f"{closing_rule_prompt()}"
    )


def _format_block(label: str, format_text: str, hook_name: str, allow_analogy: bool) -> str:
    analogy_line = (
        "- Optional: include one tight analogy if it uses drawable imagery (max one short clause).\n"
        if allow_analogy
        else "- Do NOT use analogies in this variant.\n"
    )
    return (
        f"Variant {label} guardrails:\n"
        f"- Hook type: {hook_name}. Show it in the first 3 words.\n"
        f"{format_text}"
        f"{analogy_line}"
        "- Prefer verbs and nouns over adjectives. If a descriptor isn't measurable, replace it with an action.\n"
    )


def _enforce_variant_compliance(
    label: str,
    draft: str,
    format_profile: Optional[FormatProfile],
    allow_analogy: bool,
) -> None:
    issues = detect_banned_elements(draft)
    if issues:
        raise StyleRejection(f"Variant {label} rejected: {', '.join(issues)}.")

    if format_profile:
        is_valid, reason = validate_format(draft, format_profile)
        if not is_valid:
            if format_profile.mandatory:
                raise StyleRejection(f"Variant {label} broke mandatory format '{format_profile.name}': {reason}.")
            else:
                logger.warning(f"Variant {label} did not fully comply with optional format '{format_profile.name}': {reason}. Allowing to pass.")

    analogy_hits = count_analogy_markers(draft)
    if allow_analogy:
        if analogy_hits > 1:
            raise StyleRejection(f"Variant {label} uses too many analogies ({analogy_hits}).")
    else:
        if analogy_hits:
            raise StyleRejection(f"Variant {label} must not use analogies.")


DEFAULT_POST_CATEGORIES: List[Dict[str, str]] = [
    {
        "key": "contrast_statement",
        "name": "Contrast Statement",
        "pattern": (
            "Present two opposing ideas to create an instant reveal. Invalidate a common approach (Don't do X), then "
            "present a stronger alternative (Do Y) — position Y as the obvious solution."
        ),
    },
    {
        "key": "perspective_reframe",
        "name": "Perspective Reframe",
        "pattern": (
            "Start with a universal truth the reader recognizes. Introduce a twist that reframes it — turn a negative "
            "(struggle) into a necessary element for a positive outcome (victory)."
        ),
    },
    {
        "key": "friction_reduction",
        "name": "Friction Reduction Argument",
        "pattern": (
            "Directly address analysis paralysis or fear to start. Break an intimidating goal into an absurdly small, "
            "manageable first step that motivates immediate action."
        ),
    },
    {
        "key": "identity_redefinition",
        "name": "Identity Redefinition",
        "pattern": (
            "Dismantle a limiting label (e.g., 'I'm not a salesperson'). Replace it with a simpler, authentic requirement "
            "that feels attainable and aligned with the reader's identity."
        ),
    },
    {
        "key": "parallel_contrast_aphorism",
        "name": "Parallel Contrast Aphorism",
        "pattern": (
            "Use parallel, aphoristic contrast to juxtapose two ideas. Start from a familiar saying (If A is B), then "
            "present a surprising counterpart (then C is D). Keep symmetry and punch."
        ),
    },
    {
        "key": "demonstrative_principle",
        "name": "Demonstrative Principle",
        "pattern": (
            "Teach a copywriting rule by showing. Contrast a 'bad' version (feature) with a 'good' version (benefit), "
            "then conclude with the principle demonstrated."
        ),
    },
    {
        "key": "counterintuitive_principle",
        "name": "Counterintuitive Principle",
        "pattern": (
            "State a counterintuitive rule as a near-universal law that challenges a popular belief. Push the reader to "
            "adopt a more effective method by reframing the goal."
        ),
    },
    {
        "key": "process_promise",
        "name": "Process Promise",
        "pattern": (
            "Validate the reader's frustration: change isn't instant. Offer a future promise — tiny, consistent effort adds up "
            "to a total transformation. Encourage patience and trust in the process."
        ),
    },
    {
        "key": "common_villain_exposure",
        "name": "Common Villain Exposure",
        "pattern": (
            "Recreate a recognizable negative scenario the reader detests. Expose and criticize the shared villain to build "
            "instant trust, positioning the writer as an ally."
        ),
    },
    {
        "key": "hidden_benefits_reveal",
        "name": "Hidden Benefits Reveal",
        "pattern": (
            "Start with a promise to reveal the non-obvious value of an action. Use a short list to enumerate specific, "
            "unexpected benefits — make the abstract tangible."
        ),
    },
    {
        "key": "values_manifesto",
        "name": "Values Manifesto",
        "pattern": (
            "Redefine a popular idea with a value hierarchy in a compact list. Use A > B comparisons to prioritize deep "
            "principles over superficial alternatives."
        ),
    },
    {
        "key": "delayed_gratification_formula",
        "name": "Delayed Gratification Formula",
        "pattern": (
            "State a direct cause-effect between present sacrifice and future reward. Structure like: Do the hard thing today, "
            "get the desired outcome tomorrow. Motivate disciplined action."
        ),
    },
    {
        "key": "excuse_invalidation",
        "name": "Excuse Invalidation",
        "pattern": (
            "Identify a common external excuse (the blamed villain). Then absolve it and redirect responsibility to an internal "
            "action or inaction, empowering the reader."
        ),
    },
    {
        "key": "revealing_definition",
        "name": "Revealing Definition",
        "pattern": (
            "Redefine a known concept with a sharp metaphor that reveals its overlooked essence, raising its perceived value."
        ),
    },
    {
        "key": "fundamental_maxim",
        "name": "Fundamental Maxim",
        "pattern": (
            "Present a core principle as a non-negotiable rule of the domain. Reset priorities by exposing the true hierarchy."
        ),
    },
    {
        "key": "paradox_statement",
        "name": "Paradoxical Statement",
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


def _ensure_question_at_end(text: str) -> str:
    stripped = text.rstrip()
    if stripped.endswith("?"):
        return stripped[:-1] + "."
    return stripped.rstrip(".!") + "."


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


def _build_ab_prompt(
    topic_abstract: str,
    context: PromptContext,
    shared_rules: str,
    variant_blocks: List[str],
) -> str:
    sections = [
        "You are a ghostwriter. Your task is to write TWO distinct alternatives for a tweet based on the topic below. "
        "Obey the contract, the ICP, and the formatting guardrails exactly. No extra narration.",
        "**Contract for style and tone:**",
        context.contract,
        "---",
        "**Complementary polish guardrails (do not override the contract/ICP):**",
        context.final_guidelines,
        "---",
        f"**Topic:** {topic_abstract}",
        shared_rules,
    ]
    sections.extend(variant_blocks)
    sections.append(
        "**CRITICAL OUTPUT REQUIREMENTS:**\n"
        "- Provide two high-quality, distinct alternatives in English.\n"
        "- Respect the assigned format and hook for each variant.\n"
        "- Both alternatives MUST be under 280 characters.\n"
        "- Output will be parsed automatically. Do not add labels like [A] or [B]."
    )
    return "\n\n".join(sections)


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
        "Polish the text to hit a sharper NYC bar voice — smart, direct, slightly impatient — while preserving meaning.\n"
        "Respect the existing sentence count and order. Do not merge or add sentences.\n"
        "Do NOT add emojis, hashtags, or Spanish.\n\n"
        "Amplifiers (must do):\n"
        "- First sentence must stay a hard hook (no soft lead-ins).\n"
        "- Every sentence must describe something drawable; add micro-visual detail if missing.\n"
        "- **Metaphor Audit:** If any analogy is abstract or philosophical, replace it with a direct, literal statement.\n"
        "- If you see commas or conjunctions 'and'/'or' (or 'y'/'o'), split the idea into separate sentences instead.\n"
        "- Cut adverbs ending in 'mente' or dull '-ly' fillers. Remove words: bueno, bien, solo, entonces, ya.\n"
        "- Prefer verbs and nouns over adjectives. If an adjective is vague, swap it for a concrete action/object.\n"
        "- Keep the inspirational hammer in the final sentence.\n"
        f"{comma_guard_prompt()}"
        f"{conjunction_guard_prompt()}"
        f"{visual_anchor_prompt()}"
        f"{words_blocklist_prompt()}"
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
        "Refine the text while keeping the meaning, format, and cadence intact. Maintain the same number of sentences or bullet strikes.\n"
        "Voice = NYC bar: smart, direct, a bit impatient. No emojis, no hashtags, no Spanish.\n\n"
        "Amplifiers:\n"
        "- Keep the opening punch stronger than before — hook in ≤8 words if format allows.\n"
        "- Each sentence must be drawable and rooted in physical detail.\n"
        "- **Metaphor Audit:** If any analogy is abstract or philosophical, replace it with a direct, literal statement.\n"
        "- Remove commas and conjunctions 'and'/'or' ('y'/'o'). Split thoughts into separate sentences instead.\n"
        "- Delete adverbs ending in 'mente' or filler '-ly' words. Ban: bueno, bien, solo, entonces, ya.\n"
        "- Use actions/items instead of adjectives. If an adjective is vague, replace it.\n"
        "- Ensure the final sentence is the inspirational hammer, no cheese.\n"
        f"{comma_guard_prompt()}"
        f"{conjunction_guard_prompt()}"
        f"{visual_anchor_prompt()}"
        f"{words_blocklist_prompt()}"
        "- Keep the total under 280 characters.\n\n"
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


def generate_all_variants(


    topic_abstract: str,


    context: PromptContext,


    settings: GenerationSettings,


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





    logger.info("Generating all variants via single-call multi-length prompt...")


    


    try:


        resp = llm.chat_json(
            model=settings.generation_model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.75,  # Higher temp for more creative variety
        )

        if not isinstance(resp, dict) or not all(
            k in resp for k in ["draft_short", "draft_mid", "draft_long"]
        ):
            raise StyleRejection(
                "LLM failed to produce all three drafts in a single call."
            )

        drafts = {
            "short": str(resp.get("draft_short", "")).strip(),
            "mid": str(resp.get("draft_mid", "")).strip(),
            "long": str(resp.get("draft_long", "")).strip(),
        }

        if not all(drafts.values()):
            raise StyleRejection(
                f"LLM produced one or more empty drafts. Got keys: {list(drafts.keys())}"
            )

        logger.info(
            f"[PERF] Single-call for all variants took {time.time() - start_time:.2f} seconds."
        )

        return drafts





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
    hook_idx = _pick_hooks_for_variants(rng, 1)[0]
    hook = HOOK_GUIDELINES[hook_idx]

    excerpt = _compact_text(source_text, limit=1200)
    key_terms = _extract_key_terms(excerpt)
    normalized_key_terms = [_normalize_token(term) for term in key_terms]
    normalized_key_terms = [term for term in normalized_key_terms if term and len(term) >= 3]
    # --- NEW PROMPT ARCHITECTURE (50/50 SPLIT) ---
    # Path A: End with a high-impact question (50% of the time)
    # Path B: End with a high-value statement (50% of the time)
    use_question = rng.random() < 0.5

    if use_question:
        closing_instruction = "5. CRITICAL: End with an ultra-intelligent, non-obvious question that 90% of people wouldn't think to ask. This question should spark genuine conversation, not be a generic 'What do you think?'."
    else:
        closing_instruction = "5. CRITICAL: The comment MUST stand on its own as a complete piece of high-value insight. It should not ask a question."

    prompt = f"""
We are replying to the following post. Draft ONE short, insightful comment.

POST (raw):
\"\"\"{excerpt}\"\"\"

**Core Directives:**
- Voice: "Perceptive & Constructive" (NOT "NYC bar sharp"). Your tone is insightful, direct, and conversational, but NEVER aggressive or diagnostic towards the author.
- Goal: Audience Acquisition ("The Smartest Guest"). Your job is NOT to diagnose the author. Your job is to provide a sharp, operational insight that makes the author's AUDIENCE curious about who you are.

**New Prompt Architecture: The "Connection Principle" (v4.0)**

**Core Directive: ZERO-RISK AUGMENTATION.**
Your primary function is to act as a strategic filter. A "NO_COMMENT" output is a successful execution of this filter. Your default state is silence unless a high-quality, non-conflicting connection is identified.

**Internal Monologue (Chain of Thought):**
You must follow this exact logic before generating any output.

1.  **Internal Step 1: Identify the Author's Core Assertion.** State, internally, the fundamental premise of the original post.
2.  **Internal Step 2: Assess Relevance.** Does the assertion directly relate to our ICP and core doctrines (systems, operations, productivity for solopreneurs)? If not, abort.
3.  **Internal Step 3: Assess Doctrinal Alignment.** Does the assertion contradict our core principles (e.g., promoting "hustle" over systems)? If yes, abort.
4.  **Internal Step 4: Find the Connection.** If the assertion is both relevant and non-contradictory, find the connecting principle from our doctrine that the author's idea perfectly illustrates.
5.  **Internal Step 5: Synthesize and Generate.** Synthesize the connection into a comment that starts with unambiguous validation.

**Generation Mandate & Output Format:**
Return ONLY a strict JSON object based on the outcome of your internal monologue.

- If you aborted in Step 2 or 3, return:
  `{{"status": "NO_COMMENT", "reason": "NOT_RELEVANT_TO_ICP" | "DIRECT_DOCTRINAL_CONFLICT"}}`

- If you successfully reached Step 5, return:
  `{{"status": "COMMENT", "comment": "<Your synthesized comment here>"}}`

**Gold Standard Example (The result of a successful Synthesis):**
- **Author's Point (Implicit):** "Consistency is a habit."
- **System's Output:** `{"status": "COMMENT", "comment": "100% this. That habit is the foundation of a powerful system. Habits provide the discipline; systems provide the leverage. What's the first bottleneck most people face when trying to turn that daily habit into a scalable system?"}`

**Final Output Constraints (for "COMMENT" status):**
- The entire comment MUST be a single, dense paragraph.
- The tone must be "Perceptive & Constructive."
- {closing_instruction}
- Stay under 140 characters.
- English only. No emojis or hashtags.
- Ban these words: {", ".join(sorted(BANNED_WORDS))}.

**Example of a Statement-based Output (Path B):**
That's a key insight. Often, what we call 'procrastination' is just a symptom of a bad system. Many times it's not laziness, it's paralysis: people are trying to do 50 things at once instead of focusing on the single next step.

**Technical Guardrails (Non-negotiable):**
- Stay under 140 characters.
- English only. No emojis or hashtags.
- Ban these words: {", ".join(sorted(BANNED_WORDS))}.
"""
    if key_terms:
        prompt += (
            "\nYou MUST explicitly reference at least one of these terms (quote or clearly paraphrase) to prove we read the post: "
            + ", ".join(key_terms)
            + "."
        )
    if assessment and assessment.hook:
        prompt += f"\nFocus your reply on this wedge: {assessment.hook}\n"
    if assessment and assessment.risk:
        prompt += f"\nAvoid this pitfall: {assessment.risk}\n"

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
    except Exception:
        comment = ""
        insight = None

    if not comment:
        raise StyleRejection("LLM did not generate a valid comment.")

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
    metadata["hook"] = hook.name

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

    user_prompt = f"""
We are drafting content for a fractional COO persona. Use verbalized sampling to explore {max_angles} low-probability hooks (p < 0.15) about:

TOPIC: {topic_abstract}
"""
    if rag_context:
        user_prompt += "\\nInspirational Context:\\n" + "\\n".join(f"- {doc}" for doc in rag_context) + "\\n"

    user_prompt += """
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

    user_prompt = f"""
Topic: {topic_abstract}
"""
    if rag_context:
        user_prompt += "\nInspirational Context:\n" + "\n".join(f"- {doc}" for doc in rag_context) + "\n"
        
    user_prompt += """
1. Describe the mainstream narrative most creators repeat about this topic (≤160 chars).
2. Describe a contrarian/orthogonal narrative that a fractional COO should push (≤160 chars).
3. Decide which narrative hits the ICP harder and explain why in ≤160 chars.

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
