"""
Simple tweet generator - follows ONLY the Elastic Voice Contract.
No hardcoded rules, no multiple evaluation layers, no complexity.

Philosophy:
- The contract is the single source of truth
- LLM knows how to follow instructions
- Validation checks contract compliance, not arbitrary rules
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


def generate_tweets(topic: str) -> Dict[str, str]:
    """
    Generate 3 tweet variants following the Elastic Voice Contract.

    This is the ONLY generation call. No separate calls for each variant.
    No hardcoded rules. Just: "follow the contract, generate 3 variants."

    Args:
        topic: The topic abstract to write about

    Returns:
        Dict with keys 'short', 'mid', 'long' containing tweet text
    """
    context = build_prompt_context()
    settings = AppSettings.load()

    prompt = f"""Generate 3 tweet variants about this topic, following the Elastic Voice Contract.

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
5. Each variant MUST be different in approach and proof

⚠️ CRITICAL LENGTH REQUIREMENTS (MUST COMPLY EXACTLY):
- SHORT: Maximum {SHORT_MAX} characters total. NOT ONE CHARACTER MORE.
- MID: Between {MID_MIN}-{MID_MAX} characters total. STAY IN THIS RANGE.
- LONG: Between {LONG_MIN}-{LONG_MAX} characters total. STAY IN THIS RANGE.

⚠️ IF YOU EXCEED THESE LIMITS, YOUR OUTPUT WILL BE REJECTED. COUNT CAREFULLY.

Return ONLY valid JSON (no markdown, no explanation):
{{
  "short": "tweet text here (≤{SHORT_MAX} chars)",
  "mid": "tweet text here ({MID_MIN}-{MID_MAX} chars)",
  "long": "tweet text here ({LONG_MIN}-{LONG_MAX} chars)"
}}

REMEMBER:
- Street-level means conversational. No corporate speak. No academic tone. Write like you're texting a smart friend.
- COUNT YOUR CHARACTERS. Each variant must fit its length requirement EXACTLY."""

    try:
        response = llm.chat_json(
            model=settings.post_model,
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=0.8,  # Higher temp for creativity
        )

        if not isinstance(response, dict):
            logger.error("LLM returned non-dict response")
            return {"short": "", "mid": "", "long": ""}

        return {
            "short": response.get("short", "").strip(),
            "mid": response.get("mid", "").strip(),
            "long": response.get("long", "").strip(),
        }

    except Exception as e:
        logger.error(f"Failed to generate tweets: {e}", exc_info=True)
        return {"short": "", "mid": "", "long": ""}


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


def compact_to_length(text: str, label: str, model: str) -> Optional[str]:
    """
    Attempt to compact text using LLM to fit within length requirements.

    Args:
        text: Text that's too long
        label: Variant type ('short', 'mid', 'long')
        model: LLM model to use for compaction

    Returns:
        Compacted text or None if failed
    """
    if label == "short":
        target_min, target_max = 0, SHORT_MAX
    elif label == "mid":
        target_min, target_max = MID_MIN, MID_MAX
    elif label == "long":
        target_min, target_max = LONG_MIN, LONG_MAX
    else:
        return None

    logger.info(f"Attempting to compact {label.upper()} variant ({len(text)} chars) to {target_min}-{target_max} range...")

    prompt = f"""Rewrite this text to fit EXACTLY within {target_min}-{target_max} characters.
Keep the core message and street-level tone. Remove filler words. Be ruthless.

Original text ({len(text)} chars):
{text}

Return ONLY the compacted text. No quotes, no explanations. Just the text."""

    try:
        compacted = llm.chat_text(
            model=model,
            messages=[{
                "role": "system",
                "content": "You are a ruthless editor. Preserve meaning while cutting words. Return plain text only."
            }, {
                "role": "user",
                "content": prompt
            }],
            temperature=0.3,  # Low temp for consistent editing
        )

        compacted = compacted.strip()
        if len(compacted) >= target_min and len(compacted) <= target_max:
            logger.info(f"✓ Successfully compacted to {len(compacted)} chars")
            return compacted
        else:
            logger.warning(f"Compaction resulted in {len(compacted)} chars (outside {target_min}-{target_max} range)")
            return None

    except Exception as e:
        logger.error(f"Failed to compact text: {e}")
        return None


def generate_and_validate(topic: str) -> TweetGeneration:
    """
    Full pipeline: generate 3 variants + validate each.

    This is the main entry point. It:
    1. Generates 3 variants in one LLM call
    2. Validates each variant (sanity + length + contract)
    3. Returns structured result

    Args:
        topic: Topic abstract to write about

    Returns:
        TweetGeneration with all 3 variants and their validation status
    """
    logger.info(f"Generating tweets for topic: {topic[:100]}...")

    # Generate all 3 variants in one call
    variants_raw = generate_tweets(topic)

    results = {}

    for label in ["short", "mid", "long"]:
        text = variants_raw.get(label, "")
        length = len(text)

        logger.info(f"Validating {label.upper()} variant ({length} chars)...")

        # Sanity check
        valid, reason = basic_sanity_check(text)
        if not valid:
            logger.warning(f"{label.upper()} failed sanity check: {reason}")
            results[label] = TweetVariant(
                text=text,
                label=label,
                valid=False,
                length=length,
                failure_reason=reason
            )
            continue

        # Length check with automatic compaction if needed
        valid, reason = validate_length(text, label)
        if not valid:
            logger.warning(f"{label.upper()} failed length check: {reason}")

            # Try to compact using LLM
            settings = AppSettings.load()
            compacted = compact_to_length(text, label, settings.post_model)

            if compacted:
                # Re-validate compacted text
                valid_compacted, reason_compacted = validate_length(compacted, label)
                if valid_compacted:
                    logger.info(f"✓ {label.upper()} compacted successfully from {len(text)} to {len(compacted)} chars")
                    text = compacted  # Use compacted version
                    length = len(text)
                else:
                    logger.warning(f"{label.upper()} compaction failed validation: {reason_compacted}")
                    results[label] = TweetVariant(
                        text=text,
                        label=label,
                        valid=False,
                        length=length,
                        failure_reason=f"Compaction failed: {reason_compacted}"
                    )
                    continue
            else:
                # Compaction failed, mark as invalid
                results[label] = TweetVariant(
                    text=text,
                    label=label,
                    valid=False,
                    length=length,
                    failure_reason=reason
                )
                continue

        # Contract validation
        validation = validate_against_contract(text, label)
        contract_passed = validation.get("cumple_contrato", False)

        if not contract_passed:
            reason = validation.get("razonamiento", "Failed contract validation")
            logger.warning(f"{label.upper()} failed contract validation: {reason}")
        else:
            logger.info(f"{label.upper()} passed all validations ✓")

        results[label] = TweetVariant(
            text=text,
            label=label,
            valid=contract_passed,
            length=length,
            validation_details=validation,
            failure_reason=None if contract_passed else validation.get("razonamiento")
        )

    generation = TweetGeneration(
        short=results["short"],
        mid=results["mid"],
        long=results["long"]
    )

    valid_count = len(generation.get_valid_variants())
    logger.info(f"Generation complete: {valid_count}/3 variants passed validation")

    return generation
