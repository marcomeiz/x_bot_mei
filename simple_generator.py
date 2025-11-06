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

    prompt = f"""⚠️ CRITICAL: STRICT CHARACTER LIMITS - Count every character carefully!

━━━ LENGTH REQUIREMENTS (NON-NEGOTIABLE) ━━━
• SHORT: Maximum 140 characters (not 141, not 150 - EXACTLY 140 or less)
• MID: Between 140-230 characters (must be in this range)
• LONG: Between 240-280 characters (must be in this range)

Each character counts. Spaces count. Punctuation counts.
If you write 141 chars for SHORT → INVALID. If you write 231 for MID → INVALID.

━━━ TOPIC ━━━
{topic}

<STYLE_CONTRACT>
{context.contract}
</STYLE_CONTRACT>

<TARGET_AUDIENCE>
{context.icp}
</TARGET_AUDIENCE>

━━━ CONTENT REQUIREMENTS ━━━
1. Follow ALL 10 criteria from Contract Section 8: Quality Check
2. Street-level tone: Write like texting a smart friend. No corporate speak.
3. Include concrete proof: numbers, examples, vivid scenarios
4. Create tension with contrast, paradox, or inversion
5. Each variant MUST be different in approach and proof

━━━ CHARACTER COUNT EXAMPLES ━━━
SHORT (140 chars max):
"Most founders think investors want perfect pitch decks. Wrong. They want proof you can sell. I raised $2M with a 3-slide deck." (132 chars)

MID (140-230 chars):
"You don't need venture capital to build a $10M company. I bootstrapped to $8M ARR with zero funding. Here's what VCs won't tell you: paying customers beat pitch decks every time. Traction > slides." (201 chars)

━━━ BEFORE SUBMITTING: COUNT CHARACTERS ━━━
Check: Does SHORT have ≤140 chars? Does MID have 140-230? Does LONG have 240-280?
If any variant exceeds limits → CUT WORDS until it fits.

Return ONLY valid JSON (no markdown):
{{
  "short": "your tweet text here",
  "mid": "your tweet text here",
  "long": "your tweet text here"
}}"""

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

    Requirements:
    - SHORT: ≤140 chars
    - MID: 140-230 chars
    - LONG: 240-280 chars

    Returns:
        (is_valid, failure_reason)
    """
    length = len(text)

    if label == "short":
        if length > 140:
            return False, f"Too long for SHORT: {length} > 140 chars"
    elif label == "mid":
        if length < 140 or length > 230:
            return False, f"Wrong length for MID: {length} (need 140-230 chars)"
    elif label == "long":
        if length < 240 or length > 280:
            return False, f"Wrong length for LONG: {length} (need 240-280 chars)"

    return True, ""


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

        # Length check
        valid, reason = validate_length(text, label)
        if not valid:
            logger.warning(f"{label.upper()} failed length check: {reason}")
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
