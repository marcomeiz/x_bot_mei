"""
Simple tweet generator - follows ONLY the Elastic Voice Contract.
Estrategia 1→N: Generate LONG first, then derive MID and SHORT via truncation.

Philosophy:
- The contract is the single source of truth
- LLM knows how to follow instructions
- Validation checks contract compliance, not arbitrary rules
- Generate once (LONG), derive twice (MID + SHORT) = minimal LLM calls

Strategy (1→N):
1. Generate LONG variant (265-275 chars, target ~270)
2. Validate LONG against contract
3. If fails → refine once with strict instructions
4. If still fails → abort with clear message
5. If passes → derive MID (180-220) and SHORT (≤140) via heuristic truncation
6. Return all 3 variants

Benefits:
- 66% fewer LLM generation calls (1 instead of 3)
- Coherent voice across variants (all derived from same source)
- Fast derivation via truncation (no LLM calls for MID/SHORT)
- Refinement safety net for LONG failures
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from llm_fallback import llm
from logger_config import logger
from persona import get_style_contract_text
from prompt_context import build_prompt_context
from src.settings import AppSettings

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
class TweetGeneration:
    """Result of generating 3 tweet variants."""
    short: TweetVariant
    mid: TweetVariant
    long: TweetVariant

    def get_valid_variants(self) -> List[TweetVariant]:
        """Return only the valid variants."""
        return [v for v in [self.short, self.mid, self.long] if v.valid]

    def all_valid(self) -> bool:
        """Check if all 3 variants are valid."""
        return all(v.valid for v in [self.short, self.mid, self.long])


def generate_long_variant(topic: str, attempt: int = 1) -> str:
    """
    Generate ONLY the LONG variant following the Elastic Voice Contract.

    Estrategia 1→N: Start with the best LONG variant, then derive MID/SHORT from it.

    Target: 270±5 chars (265-275 range) for optimal derivation.

    Args:
        topic: The topic abstract to write about
        attempt: Attempt number (1 = initial, 2 = refinement)

    Returns:
        LONG tweet text (or empty string on failure)
    """
    context = build_prompt_context()
    settings = AppSettings.load()

    # Target 270 chars for optimal balance (can derive both shorter variants cleanly)
    target_min, target_max = 265, 275

    strictness_note = ""
    if attempt > 1:
        strictness_note = f"""
⚠️⚠️⚠️ REFINEMENT ATTEMPT {attempt} - LAST CHANCE ⚠️⚠️⚠️
Your previous attempt failed validation. This is your FINAL attempt.
You MUST generate text between {target_min}-{target_max} characters.
Count every single character. If you fail again, generation will abort."""

    prompt = f"""Generate ONE tweet (LONG format) about this topic, following the Elastic Voice Contract.

<TOPIC>
{topic}
</TOPIC>

<STYLE_CONTRACT>
{context.contract}
</STYLE_CONTRACT>

<TARGET_AUDIENCE>
{context.icp}
</TARGET_AUDIENCE>

REQUIREMENTS:
1. Follow ALL 10 criteria from Contract Section 8: Quality Check
2. Use street-level tone (Section 2: "Write like you talk")
3. Include concrete proof: numbers, examples, or vivid scenarios
4. Create tension with contrast, paradox, or inversion

⚠️ CRITICAL LENGTH REQUIREMENT (MUST COMPLY EXACTLY):
TARGET: {target_min}-{target_max} characters total (aim for ~270 chars)
This is NOT a suggestion - it's a hard requirement.
{strictness_note}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "tweet": "your tweet text here ({target_min}-{target_max} chars)"
}}

REMEMBER:
- Street-level means conversational. No corporate speak. No academic tone.
- COUNT YOUR CHARACTERS CAREFULLY. You MUST stay within {target_min}-{target_max} range."""

    try:
        response = llm.chat_json(
            model=settings.post_model,
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=0.7 if attempt == 1 else 0.5,  # Lower temp on refinement for precision
        )

        if not isinstance(response, dict):
            logger.error("LLM returned non-dict response")
            return ""

        tweet = response.get("tweet", "").strip()
        logger.info(f"Generated LONG variant (attempt {attempt}): {len(tweet)} chars")
        return tweet

    except Exception as e:
        logger.error(f"Failed to generate LONG variant (attempt {attempt}): {e}", exc_info=True)
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

    if label == "short":
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


def generate_and_validate(topic: str) -> TweetGeneration:
    """
    Estrategia 1→N: Generate LONG first, then derive MID and SHORT.

    Pipeline:
    1. Generate LONG variant (265-275 chars, target ~270)
    2. Validate LONG (sanity + length + contract)
    3. If LONG fails contract → refine once with strict instructions
    4. If refined LONG fails → abort with clear error
    5. If LONG passes → derive MID (180-220) and SHORT (≤140) via truncation
    6. Validate derived variants (sanity + length only)
    7. Return all 3 variants

    Args:
        topic: Topic abstract to write about

    Returns:
        TweetGeneration with all 3 variants and their validation status
    """
    logger.info(f"[Estrategia 1→N] Generating tweets for topic: {topic[:100]}...")

    results = {}

    # === STEP 1: Generate LONG variant ===
    logger.info("Step 1: Generating LONG variant (265-275 chars target)...")
    long_text = generate_long_variant(topic, attempt=1)

    if not long_text:
        logger.error("Failed to generate LONG variant")
        # Return all invalid
        return TweetGeneration(
            short=TweetVariant("", "short", False, 0, failure_reason="LONG generation failed"),
            mid=TweetVariant("", "mid", False, 0, failure_reason="LONG generation failed"),
            long=TweetVariant("", "long", False, 0, failure_reason="LONG generation failed"),
        )

    # === STEP 2: Validate LONG ===
    logger.info(f"Step 2: Validating LONG variant ({len(long_text)} chars)...")

    # Sanity check
    valid, reason = basic_sanity_check(long_text)
    if not valid:
        logger.error(f"LONG failed sanity check: {reason}")
        return TweetGeneration(
            short=TweetVariant("", "short", False, 0, failure_reason=f"LONG sanity check failed: {reason}"),
            mid=TweetVariant("", "mid", False, 0, failure_reason=f"LONG sanity check failed: {reason}"),
            long=TweetVariant(long_text, "long", False, len(long_text), failure_reason=reason),
        )

    # Length check (target 265-275)
    if len(long_text) < 265 or len(long_text) > 275:
        logger.warning(f"LONG length out of target range: {len(long_text)} (need 265-275)")
        # Not a hard failure - we'll try refinement if contract fails

    # Contract validation
    validation = validate_against_contract(long_text, "long")
    contract_passed = validation.get("cumple_contrato", False)

    # === STEP 3: Refinement if needed ===
    if not contract_passed:
        logger.warning(f"LONG failed contract validation: {validation.get('razonamiento', 'Unknown reason')}")
        logger.info("Step 3: Refining LONG variant (attempt 2)...")

        # Refine once
        long_text_refined = generate_long_variant(topic, attempt=2)

        if not long_text_refined:
            logger.error("Refinement failed: could not generate LONG variant")
            return TweetGeneration(
                short=TweetVariant("", "short", False, 0, failure_reason="LONG refinement failed"),
                mid=TweetVariant("", "mid", False, 0, failure_reason="LONG refinement failed"),
                long=TweetVariant(long_text, "long", False, len(long_text),
                                failure_reason=validation.get("razonamiento", "Contract validation failed"),
                                validation_details=validation),
            )

        # Validate refined LONG
        valid_refined, reason_refined = basic_sanity_check(long_text_refined)
        if not valid_refined:
            logger.error(f"Refined LONG failed sanity check: {reason_refined}")
            return TweetGeneration(
                short=TweetVariant("", "short", False, 0, failure_reason=f"Refined LONG sanity failed: {reason_refined}"),
                mid=TweetVariant("", "mid", False, 0, failure_reason=f"Refined LONG sanity failed: {reason_refined}"),
                long=TweetVariant(long_text_refined, "long", False, len(long_text_refined), failure_reason=reason_refined),
            )

        validation_refined = validate_against_contract(long_text_refined, "long")
        contract_passed_refined = validation_refined.get("cumple_contrato", False)

        if not contract_passed_refined:
            logger.error("❌ Refinement failed: LONG still does not pass contract after 2 attempts")
            failure_msg = f"Failed contract validation after refinement. Reason: {validation_refined.get('razonamiento', 'Unknown')}"
            return TweetGeneration(
                short=TweetVariant("", "short", False, 0, failure_reason=failure_msg),
                mid=TweetVariant("", "mid", False, 0, failure_reason=failure_msg),
                long=TweetVariant(long_text_refined, "long", False, len(long_text_refined),
                                failure_reason=validation_refined.get("razonamiento"),
                                validation_details=validation_refined),
            )

        # Refinement succeeded!
        logger.info("✓ Refined LONG passed contract validation")
        long_text = long_text_refined
        validation = validation_refined
    else:
        logger.info("✓ LONG passed contract validation on first attempt")

    # === STEP 4: Store validated LONG ===
    results["long"] = TweetVariant(
        text=long_text,
        label="long",
        valid=True,
        length=len(long_text),
        validation_details=validation,
        failure_reason=None
    )

    # === STEP 5: Derive MID and SHORT via truncation ===
    logger.info("Step 5: Deriving MID and SHORT variants via heuristic truncation...")

    # Derive MID (target 200±20 = 180-220 range)
    mid_text = truncate_to_length(long_text, target_max=220, target_min=180)
    logger.info(f"Derived MID: {len(mid_text)} chars")

    # Derive SHORT (≤140 chars)
    short_text = truncate_to_length(long_text, target_max=SHORT_MAX, target_min=0)
    logger.info(f"Derived SHORT: {len(short_text)} chars")

    # === STEP 6: Validate derived variants (sanity + length only, no contract) ===
    # MID validation
    valid_mid, reason_mid = basic_sanity_check(mid_text)
    if valid_mid:
        valid_mid, reason_mid = validate_length(mid_text, "mid")

    if valid_mid:
        logger.info("✓ MID derived successfully")
        results["mid"] = TweetVariant(mid_text, "mid", True, len(mid_text))
    else:
        logger.warning(f"MID derivation issue: {reason_mid}")
        results["mid"] = TweetVariant(mid_text, "mid", False, len(mid_text), failure_reason=reason_mid)

    # SHORT validation
    valid_short, reason_short = basic_sanity_check(short_text)
    if valid_short:
        valid_short, reason_short = validate_length(short_text, "short")

    if valid_short:
        logger.info("✓ SHORT derived successfully")
        results["short"] = TweetVariant(short_text, "short", True, len(short_text))
    else:
        logger.warning(f"SHORT derivation issue: {reason_short}")
        results["short"] = TweetVariant(short_text, "short", False, len(short_text), failure_reason=reason_short)

    # === STEP 7: Return result ===
    generation = TweetGeneration(
        short=results["short"],
        mid=results["mid"],
        long=results["long"]
    )

    valid_count = len(generation.get_valid_variants())
    logger.info(f"[Estrategia 1→N] Generation complete: {valid_count}/3 variants valid")

    return generation
