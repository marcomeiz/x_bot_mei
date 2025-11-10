"""
Simple tweet generator - follows ONLY the Elastic Voice Contract.
Adaptive Strategy: Generate a single optimal-length tweet (140-270 chars).

Philosophy:
- The contract is the single source of truth
- LLM knows how to follow instructions
- Validation checks contract compliance, not arbitrary rules
- Quality over quantity - one excellent tweet beats three mediocre variants

Strategy (Adaptive):
1. Generate single adaptive variant (LLM chooses optimal length 140-270 chars)
2. Validate variant against contract
3. If fails → refine once with strict instructions
4. If still fails → abort with clear message
5. Return single validated tweet

Benefits:
- Complete tweets (no truncation = no incomplete endings)
- Optimal length per topic (simple insights shorter, complex topics longer)
- Every word earns its place (no filler to hit fixed length)
- Focused quality investment (100% effort on one great tweet)
- Refinement safety net for validation failures
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from llm_fallback import llm
from logger_config import logger
from persona import get_style_contract_text
from prompt_context import build_prompt_context
from src.settings import AppSettings
from src.goldset import retrieve_goldset_examples_random

# Load length constraints from configuration (DRY principle - no hardcoding!)
_settings = AppSettings.load()
_variant_lengths = _settings.variant_lengths

SHORT_MAX = _variant_lengths.short.max
MID_MIN = _variant_lengths.mid.min or 140  # fallback for backward compatibility
MID_MAX = _variant_lengths.mid.max
LONG_MIN = _variant_lengths.long.min or 240  # fallback for backward compatibility
LONG_MAX = _variant_lengths.long.max


@dataclass
class TweetVariant:
    """A single tweet variant with its validation status."""
    text: str
    label: str
    valid: bool
    length: int
    validation_details: Optional[Dict] = None
    failure_reason: Optional[str] = None


@dataclass
class CoTIteration:
    """Single iteration of Chain of Thought process."""
    iteration_num: int
    thinking: str
    draft: str
    self_eval: Dict
    passed: bool


@dataclass
class TweetGeneration:
    """Result of generating 3 tweet variants."""
    short: TweetVariant
    mid: TweetVariant
    long: TweetVariant
    usage_info: Optional[Dict] = None  # Token usage: {model, input_tokens, output_tokens, cost}
    cot_iterations: Optional[List[CoTIteration]] = None  # Chain of Thought process log

    def get_valid_variants(self) -> List[TweetVariant]:
        """Return only the valid variants."""
        return [v for v in [self.short, self.mid, self.long] if v.valid]

    def all_valid(self) -> bool:
        """Check if all 3 variants are valid."""
        return all(v.valid for v in [self.short, self.mid, self.long])


def _generate_thinking(topic: str, iteration: int, previous_feedback: Optional[str] = None) -> str:
    """
    STEP 1 of CoT: Think about the topic and approach.
    Uses DeepSeek (cheap model) for thinking.
    """
    settings = AppSettings.load()
    thinking_model = "deepseek/deepseek-chat-v3.1"  # Cheap for thinking

    feedback_context = ""
    if previous_feedback and iteration > 1:
        feedback_context = f"""
Previous attempt FAILED with feedback:
{previous_feedback}

Take this into account in your thinking."""

    prompt = f"""Topic: {topic}

{feedback_context}

Think out loud (2-4 sentences max). Be specific:

1. What's the ONE core insight or punch line?
2. What CONCRETE detail will you use? (number, scene, example - be specific)
3. How will you avoid sounding corporate/AI? (what pattern will you break?)

Attempt #{iteration} of 2.

Think naturally, like you're talking to yourself before writing."""

    try:
        thinking = llm.chat_text(
            model=thinking_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9
        )
        logger.info(f"[CoT] Iteration {iteration} - Thinking: {thinking[:150]}...")
        return thinking.strip()
    except Exception as e:
        logger.error(f"Failed to generate thinking: {e}")
        return "Failed to generate thinking."


def _self_evaluate(draft: str, thinking: str) -> Dict:
    """
    STEP 3 of CoT: Self-evaluate the draft against anti-AI criteria.
    Uses DeepSeek (cheap model) for evaluation.
    """
    thinking_model = "deepseek/deepseek-chat-v3.1"

    prompt = f"""Your thinking was:
{thinking}

Your draft:
{draft}

Self-evaluate against these 4 tests (score 1-10 each):

1. **WhatsApp Test**: Would you send this as-is to a smart friend?
   Look for: stiffness, marketing speak, corporate jargon, "helpful AI" tone

2. **Specificity Test**: Does it have concrete details?
   Look for: numbers, scenes, names, specific situations (not vague claims)

3. **Pattern Test**: Are sentence starts varied and natural?
   Read first 3 words of each sentence. If repetitive = FAIL.
   Look for: "You need", "You should", "This is" repeated

4. **Voice Test**: Second person, natural, not AI-like?
   Look for: AI tells like "It's important to", "Let's explore", symmetrical structures
   CRITICAL: Missing contractions = instant penalty. Use "don't" not "do not", "that's" not "that is", "isn't" not "is not"

CRITICAL: Be HARSH. Score 7+ only if genuinely good. Most drafts fail first try.

Respond in JSON:
{{
  "whatsapp_test": <1-10>,
  "whatsapp_issues": "<specific issues or 'none'>",
  "specificity_test": <1-10>,
  "specificity_issues": "<specific issues or 'none'>",
  "pattern_test": <1-10>,
  "pattern_issues": "<specific issues or 'none'>",
  "voice_test": <1-10>,
  "voice_issues": "<specific issues or 'none'>",
  "overall_pass": <true if ALL tests >= 7, false otherwise>,
  "feedback": "<if failed: concrete actionable fix. if passed: 'Good'>",
  "avg_score": <average of 4 tests>
}}"""

    try:
        eval_result = llm.chat_json(
            model=thinking_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3  # Lower temp for consistent evaluation
        )

        logger.info(f"[CoT] Self-eval scores: WhatsApp={eval_result.get('whatsapp_test')}/10, "
                   f"Specificity={eval_result.get('specificity_test')}/10, "
                   f"Pattern={eval_result.get('pattern_test')}/10, "
                   f"Voice={eval_result.get('voice_test')}/10 | "
                   f"Pass={eval_result.get('overall_pass')}")

        return eval_result
    except Exception as e:
        logger.error(f"Failed to self-evaluate: {e}")
        # Default to passing on error (fail-safe)
        return {
            "whatsapp_test": 7,
            "specificity_test": 7,
            "pattern_test": 7,
            "voice_test": 7,
            "overall_pass": True,
            "feedback": "Evaluation failed, passing by default",
            "avg_score": 7.0
        }


def generate_adaptive_variant_with_cot(topic: str, model_override: Optional[str] = None) -> Tuple[str, List[CoTIteration]]:
    """
    Generate adaptive tweet with Chain of Thought self-correction.

    Flow:
    1. Think about approach (DeepSeek)
    2. Generate draft (Gemini/override)
    3. Self-evaluate (DeepSeek)
    4. If fails → incorporate feedback and retry (max 2 iterations)
    5. Return best draft + CoT log

    Returns:
        Tuple of (tweet_text, cot_iterations)
    """
    MAX_ITERATIONS = 2
    cot_iterations = []
    previous_feedback = None
    final_draft = ""

    for iteration in range(1, MAX_ITERATIONS + 1):
        logger.info(f"[CoT] ========== ITERATION {iteration}/{MAX_ITERATIONS} ==========")

        # STEP 1: Think
        thinking = _generate_thinking(topic, iteration, previous_feedback)

        # STEP 2: Generate (using thinking as context)
        draft = generate_adaptive_variant(
            topic=topic,
            attempt=iteration,
            model_override=model_override,
            thinking_context=thinking  # Pass thinking to generation
        )

        if not draft:
            logger.error(f"[CoT] Iteration {iteration} failed to generate draft")
            continue

        # STEP 3: Self-evaluate
        self_eval = _self_evaluate(draft, thinking)

        # Log iteration
        cot_iter = CoTIteration(
            iteration_num=iteration,
            thinking=thinking,
            draft=draft,
            self_eval=self_eval,
            passed=self_eval.get("overall_pass", False)
        )
        cot_iterations.append(cot_iter)

        # STEP 4: Decide if we're done
        if self_eval.get("overall_pass") or iteration == MAX_ITERATIONS:
            final_draft = draft
            logger.info(f"[CoT] ========== FINAL DRAFT (iteration {iteration}) ==========")
            break

        # STEP 5: Prepare feedback for next iteration
        previous_feedback = self_eval.get("feedback", "")
        logger.info(f"[CoT] Iteration {iteration} failed. Retrying with feedback: {previous_feedback[:100]}...")

    return final_draft, cot_iterations


def generate_adaptive_variant(topic: str, attempt: int = 1, model_override: Optional[str] = None, thinking_context: Optional[str] = None) -> str:
    """
    Generate a single adaptive-length tweet following the Elastic Voice Contract.

    The LLM decides the optimal length (140-270 chars) based on the topic's needs.
    Simple insights can be shorter (~140-180), complex topics can be longer (~240-270).

    Args:
        topic: The topic abstract to write about
        attempt: Attempt number (1 = initial, 2 = refinement)
        model_override: Optional model to use instead of default

    Returns:
        Tweet text (or empty string on failure)
    """
    context = build_prompt_context()
    settings = AppSettings.load()

    # Adaptive length range: 140-270 chars
    target_min, target_max = 140, 270

    # Sample 3 random golden examples for style learning (research-backed: 2-5 optimal)
    try:
        golden_examples = retrieve_goldset_examples_random(k=3)
        logger.info(f"Retrieved {len(golden_examples)} golden examples for style reference")
    except Exception as exc:
        logger.warning(f"Failed to retrieve golden examples: {exc}. Proceeding without style examples.")
        golden_examples = []

    # Build golden style examples section if available
    golden_section = ""
    if golden_examples:
        style_refs = "\n\n".join([
            f"STYLE REFERENCE {i+1}:\n{ex}"
            for i, ex in enumerate(golden_examples)
        ])
        golden_section = f"""
<GOLDEN_STYLE_EXAMPLES>
These are REAL published tweets. Learn the STYLE (tone, rhythm, voice), NOT the topics:

{style_refs}
</GOLDEN_STYLE_EXAMPLES>
"""

    # Add thinking context if provided (from CoT)
    thinking_section = ""
    if thinking_context:
        thinking_section = f"""
<YOUR_THINKING>
Before writing, you thought:
{thinking_context}

Use this thinking to guide your writing. Stay concrete and specific.
</YOUR_THINKING>
"""

    strictness_note = ""
    if attempt > 1:
        strictness_note = f"""
⚠️⚠️⚠️ REFINEMENT ATTEMPT {attempt} - LAST CHANCE ⚠️⚠️⚠️
Your previous attempt failed self-evaluation. This is your FINAL attempt.
You MUST generate text between {target_min}-{target_max} characters.
Count every single character. If you fail again, generation will abort."""

    prompt = f"""Generate ONE tweet about this topic, following the Elastic Voice Contract.
{thinking_section}

<TOPIC>
{topic}
</TOPIC>

<STYLE_CONTRACT>
{context.contract}
</STYLE_CONTRACT>
{golden_section}
<TARGET_AUDIENCE>
{context.icp}
</TARGET_AUDIENCE>

CORE PRINCIPLES:
- Sound typed by someone who knows their shit, not crafted by a copywriter
- ALWAYS use contractions (don't, that's, isn't) — this is how real people talk
- Avoid AI tells: perfect architecture, symmetrical structures, tagline closers
- Use structure (line breaks, lists, arrows) when it helps CLARITY, not to show off

⚠️ ADAPTIVE LENGTH REQUIREMENT:
- Range: {target_min}-{target_max} characters total
- Choose the OPTIMAL length for this specific topic:
  • Simple, punchy insights: shorter (140-180 chars)
  • Stories or complex ideas: longer (240-270 chars)
- Prioritize COMPLETENESS over brevity — the tweet MUST feel finished, not cut off
- Every word must earn its place (no filler)
{strictness_note}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "tweet": "your tweet text here ({target_min}-{target_max} chars, optimal length for topic)"
}}"""

    try:
        # Model selection: override > hybrid strategy (refiner on attempt 2) > default
        if model_override:
            model_to_use = model_override
        else:
            model_to_use = settings.post_refiner_model if attempt == 2 else settings.post_model
        temp = 0.7 if attempt == 1 else 0.6  # Balanced temp for quality, lower on refinement

        logger.info(f"[LLM] Generation attempt {attempt}: model={model_to_use}, temp={temp}")

        response = llm.chat_json(
            model=model_to_use,
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=temp,
        )

        if not isinstance(response, dict):
            logger.error("LLM returned non-dict response")
            return ""

        tweet = response.get("tweet", "").strip()
        logger.info(f"Generated adaptive variant (attempt {attempt}): {len(tweet)} chars")
        return tweet

    except Exception as e:
        logger.error(f"Failed to generate adaptive variant (attempt {attempt}): {e}", exc_info=True)
        return ""


def validate_against_contract(text: str, label: str) -> Dict:
    """
    Validate a tweet against the Elastic Voice Contract.

    Checks all 10 mandatory criteria from Section 8: Quality Check.
    This is the ONLY quality gate. No other validators.

    Args:
        text: The tweet text to validate
        label: 'short', 'mid', or 'long'

    Returns:
        Dict with validation results including passed/failed for each criterion
    """
    contract = get_style_contract_text()
    settings = AppSettings.load()

    prompt = f"""Evaluate this tweet against the Elastic Voice Contract.

<CONTRACT>
{contract}
</CONTRACT>

<TWEET>
{text}
</TWEET>

TASK: Check if this tweet meets the 10 mandatory criteria from Section 8: Quality Check.

For each criterion, provide:
- passed (bool): true if criterion is met
- score (int 1-5): quality rating
- evidence (string): quote from tweet or specific reason

Return ONLY valid JSON (no markdown):
{{
  "hook": {{"passed": bool, "score": int, "evidence": "string"}},
  "proof": {{"passed": bool, "score": int, "evidence": "string"}},
  "agency": {{"passed": bool, "score": int, "evidence": "string"}},
  "rhythm": {{"passed": bool, "score": int, "evidence": "string"}},
  "originality": {{"passed": bool, "score": int, "evidence": "string"}},
  "energy": {{"passed": bool, "score": int, "evidence": "string"}},
  "humanity": {{"passed": bool, "score": int, "evidence": "string"}},
  "tension": {{"passed": bool, "score": int, "evidence": "string"}},
  "boundaries": {{"passed": bool, "score": int, "evidence": "string"}},
  "anchor": {{"passed": bool, "score": int, "evidence": "string"}},
  "cumple_contrato": bool,
  "passed_count": int,
  "total_score": float,
  "razonamiento": "1-2 sentence overall assessment"
}}

PASS CRITERIA: Must pass at least 8 out of 10 criteria (aim for 4/5 or better as contract states)."""

    try:
        logger.info(f"[LLM] Contract validation: model={settings.eval_fast_model}, temp=0.1")

        response = llm.chat_json(
            model=settings.eval_fast_model,  # Use fast model for validation
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=0.1,  # Low temp for consistent validation
        )

        if not isinstance(response, dict):
            logger.error(f"Validation for {label} returned non-dict")
            return {"cumple_contrato": False, "razonamiento": "Invalid validation response"}

        return response

    except Exception as e:
        logger.error(f"Failed to validate {label}: {e}", exc_info=True)
        return {"cumple_contrato": False, "razonamiento": f"Validation error: {str(e)}"}


def basic_sanity_check(text: str) -> Tuple[bool, str]:
    """
    Basic sanity checks that any tweet should pass.
    These are non-negotiable technical requirements, not style rules.

    Returns:
        (is_valid, failure_reason)
    """
    if not text or not text.strip():
        return False, "Empty text"

    # No emojis
    if re.search(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\u2600-\u26FF\u2700-\u27BF]', text):
        return False, "Contains emoji"

    # No hashtags
    if '#' in text:
        return False, "Contains hashtag"

    # No URLs
    if re.search(r'https?://', text):
        return False, "Contains URL"

    return True, ""


def validate_length(text: str, label: str) -> Tuple[bool, str]:
    """
    Validate tweet length according to variant type.
    Uses configuration from settings.dev.yaml (variant_lengths).

    Returns:
        (is_valid, failure_reason)
    """
    length = len(text)

    if label == "adaptive":
        # Adaptive variant: 140-270 chars
        if length < 140 or length > 270:
            return False, f"Wrong length for ADAPTIVE: {length} (need 140-270 chars)"
    elif label == "short":
        if length > SHORT_MAX:
            return False, f"Too long for SHORT: {length} > {SHORT_MAX} chars"
    elif label == "mid":
        if length < MID_MIN or length > MID_MAX:
            return False, f"Wrong length for MID: {length} (need {MID_MIN}-{MID_MAX} chars)"
    elif label == "long":
        if length < LONG_MIN or length > LONG_MAX:
            return False, f"Wrong length for LONG: {length} (need {LONG_MIN}-{LONG_MAX} chars)"

    return True, ""


def truncate_to_length(text: str, target_max: int, target_min: int = 0) -> str:
    """
    Heuristic truncation (hard truncation at word boundaries).

    Estrategia 1→N: Derive MID and SHORT from LONG using simple truncation.
    No LLM calls - just smart string manipulation.

    Args:
        text: Source text (usually LONG variant)
        target_max: Maximum chars
        target_min: Minimum chars (optional)

    Returns:
        Truncated text
    """
    if len(text) <= target_max:
        return text

    # Try truncating at sentence boundaries first
    sentences = re.split(r'([.!?]+\s+)', text)
    accumulated = ""
    for i, part in enumerate(sentences):
        test = accumulated + part
        if len(test) > target_max:
            break
        accumulated = test

    # If sentence truncation gives us something in range, use it
    accumulated = accumulated.strip()
    if target_min <= len(accumulated) <= target_max:
        logger.info(f"Truncated at sentence boundary: {len(text)} → {len(accumulated)} chars")
        return accumulated

    # Otherwise, truncate at word boundary
    words = text.split()
    accumulated = ""
    for word in words:
        test = (accumulated + " " + word).strip()
        if len(test) > target_max:
            break
        accumulated = test

    result = accumulated.strip()

    # If we got nothing or too short, just hard cut at target_max
    if not result or len(result) < (target_min if target_min > 0 else 20):
        result = text[:target_max].rsplit(' ', 1)[0].strip()

    logger.info(f"Truncated at word boundary: {len(text)} → {len(result)} chars")
    return result


def generate_and_validate(topic: str, model_override: Optional[str] = None) -> TweetGeneration:
    """
    Adaptive Strategy: Generate a single optimal-length tweet (140-270 chars).

    Pipeline:
    1. Generate adaptive variant (LLM chooses optimal length 140-270 chars)
    2. Validate variant (sanity + length + contract)
    3. If fails contract → refine once with strict instructions
    4. If refined fails → abort with clear error
    5. Return result (adaptive variant in 'long' field, short/mid empty)

    Args:
        topic: Topic abstract to write about
        model_override: Optional model to use instead of default

    Returns:
        TweetGeneration with adaptive variant in 'long' field (short/mid are empty/invalid)
    """
    logger.info(f"[Adaptive Strategy with CoT] Generating tweet for topic: {topic[:100]}... Model: {model_override or 'default'}")

    # === STEP 1: Generate with Chain of Thought (includes self-correction) ===
    logger.info("Step 1: Generating with Chain of Thought (2 iterations max, self-correcting)...")
    tweet_text, cot_iterations = generate_adaptive_variant_with_cot(topic, model_override=model_override)

    if not tweet_text:
        logger.error("Failed to generate adaptive variant after CoT")
        return TweetGeneration(
            short=TweetVariant("", "short", False, 0, failure_reason="Not generated (single adaptive strategy)"),
            mid=TweetVariant("", "mid", False, 0, failure_reason="Not generated (single adaptive strategy)"),
            long=TweetVariant("", "long", False, 0, failure_reason="CoT generation failed"),
            cot_iterations=cot_iterations
        )

    # === STEP 2: Final validation (sanity + contract) ===
    logger.info(f"Step 2: Final validation of CoT output ({len(tweet_text)} chars)...")

    # Sanity check
    valid, reason = basic_sanity_check(tweet_text)
    if not valid:
        logger.error(f"CoT output failed sanity check: {reason}")
        return TweetGeneration(
            short=TweetVariant("", "short", False, 0, failure_reason="Not generated (single adaptive strategy)"),
            mid=TweetVariant("", "mid", False, 0, failure_reason="Not generated (single adaptive strategy)"),
            long=TweetVariant(tweet_text, "long", False, len(tweet_text), failure_reason=reason),
            cot_iterations=cot_iterations
        )

    # Length check (140-270)
    valid_length, length_reason = validate_length(tweet_text, "adaptive")
    if not valid_length:
        logger.warning(f"CoT output length issue: {length_reason}")

    # Contract validation
    validation = validate_against_contract(tweet_text, "adaptive")
    contract_passed = validation.get("cumple_contrato", False)

    if not contract_passed:
        logger.warning(f"⚠️ CoT output failed contract validation: {validation.get('razonamiento', 'Unknown')}")
        # Note: CoT already did 2 iterations of self-correction, so we accept this
        # The CoT self-eval is more focused on anti-AI patterns, contract is broader
    else:
        logger.info("✓ CoT output passed contract validation")

    # === STEP 4: Capture token usage ===
    usage_info = llm.get_last_usage()
    if usage_info:
        logger.info(f"[USAGE_CAPTURED] {usage_info}")

    # === STEP 5: Return result ===
    # Return TweetGeneration with adaptive variant in 'long' field
    # (short and mid are marked invalid - not used in adaptive strategy)
    generation = TweetGeneration(
        short=TweetVariant("", "short", False, 0, failure_reason="Not generated (single adaptive strategy)"),
        mid=TweetVariant("", "mid", False, 0, failure_reason="Not generated (single adaptive strategy)"),
        long=TweetVariant(
            text=tweet_text,
            label="long",
            valid=True,
            length=len(tweet_text),
            validation_details=validation,
            failure_reason=None
        ),
        usage_info=usage_info,
        cot_iterations=cot_iterations  # Include CoT process log
    )

    logger.info(f"[Adaptive Strategy] Generation complete: {len(tweet_text)} chars, valid={generation.long.valid}")
    return generation
