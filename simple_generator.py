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


def generate_adaptive_variant(topic: str, attempt: int = 1) -> str:
    """
    Generate a single adaptive-length tweet following the Elastic Voice Contract.

    The LLM decides the optimal length (140-270 chars) based on the topic's needs.
    Simple insights can be shorter (~140-180), complex topics can be longer (~240-270).

    Args:
        topic: The topic abstract to write about
        attempt: Attempt number (1 = initial, 2 = refinement)

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

    strictness_note = ""
    if attempt > 1:
        strictness_note = f"""
⚠️⚠️⚠️ REFINEMENT ATTEMPT {attempt} - LAST CHANCE ⚠️⚠️⚠️
Your previous attempt failed validation. This is your FINAL attempt.
You MUST generate text between {target_min}-{target_max} characters.
Count every single character. If you fail again, generation will abort."""

    prompt = f"""Generate ONE tweet about this topic, following the Elastic Voice Contract.

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

CORE PRINCIPLE: Sound typed by someone who knows their shit, not crafted by a copywriter.

The contract covers quality. Your job: avoid AI tells. Learn from these contrasts:

━━━ EXAMPLE 1: Waitlists ━━━

❌ TOO POLISHED (AI tell: perfect architecture):
"You think packing your waitlist boosts revenue. Bullshit. It floods you with flakes, kills scarcity, shreds quality. Cap it hard at 25 spots. I did—turned away 150, but those 25 converted 4x higher and raved. Scarcity sells. Discipline protects your edge."
→ Problem: 3-part parallels ("floods, kills, shreds"), two perfect closers ("Scarcity sells. Discipline protects"), every sentence serves clear purpose

✅ BETTER (sounds human):
"Waitlists are broken. I capped mine at 25 and turned away like 150 people. Those 25? 4x conversion. Not saying it's the only way but scarcity worked for me."
→ Why: Casual phrasing ("like 150"), rougher transitions, ending doesn't stick perfect landing

━━━ EXAMPLE 2: Saying No ━━━

❌ TOO STRUCTURED (AI tell: case study template):
"You're a solopreneur, not a vending machine. You say yes to every random ask because money's money. Bullshit. That's how you kill focus. Last month I turned down 4 gigs, stuck to one project. Finished my core project twice as fast, energy actually back. Protect your scope or burn out."
→ Problem: Compressed prose, perfect case study flow, every data point clean, ending too round

✅ BETTER (typed thought with structure):
"You're a solopreneur, not a vending machine.
You keep saying yes to every random ask because \"money's money\".
Bullshit. That's how you murder your focus.

Last month I drew a hard line:
- Turned down 4 off-scope gigs
- Stuck to one core project

Result: finished it in half the time, with energy left instead of brain fog.

Say it clear: protect your scope or slowly delete yourself."
→ Why: Line breaks for breathing, list when needed, less essay-like, raw ending

━━━ EXAMPLE 3: Content Creation ━━━

❌ TOO DIDACTIC (AI tell: PowerPoint structure):
"Buried in client work, ghosting your own content? That kills inbound. Truth: post 3x/week from real wins. I systematized it—10 hours/week batching. Shared how a client saved 10h/week → 7 qualified DMs in a day. Your experiences are gold. Mine them or stay invisible."
→ Problem: "Truth:" header sounds like webinar, "systematized" is corporate, data too packaged, ending like tagline

✅ BETTER (compressed natural flow):
"Buried in client work and ghosting your own content? That's why inbound is dead. Post 3x/week from real client wins. Shared how a client saved 10h/week batching email → 7 qualified DMs in 24h. Your work is gold. Use it or stay invisible."
→ Why: No didactic headers, natural flow, arrow (→) not explanation, simpler verb ("use" not "mine"), still punchy

━━━ PATTERN TO INTERNALIZE ━━━
AI writes like: Setup → Lesson Label → Perfect Case Study → Tagline Closer
Humans write like: Rough start → maybe a list if needed → story with loose edges → ending that's strong but not too clean

Use structure (line breaks, lists, arrows) when it helps CLARITY, not to show architecture.

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
}}

REMEMBER:
- Street-level means conversational. No corporate speak. No academic tone.
- Sound typed, not crafted. A little roughness is human.
- Let the topic dictate the ideal length within the {target_min}-{target_max} range.
- COUNT YOUR CHARACTERS CAREFULLY."""

    try:
        # Hybrid strategy: use post_refiner_model on attempt 2 (fallback to premium model)
        model_to_use = settings.post_refiner_model if attempt == 2 else settings.post_model
        temp = 0.8 if attempt == 1 else 0.6  # Higher temp for natural variation, slightly lower on refinement

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
        logger.info(f"[LLM] Contract validation: model={settings.eval_fast_model}, temp=0.1, max_tokens=500")

        response = llm.chat_json(
            model=settings.eval_fast_model,  # Use fast model for validation
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=0.1,  # Low temp for consistent validation
            max_tokens=500,  # Production cost control: validation JSON (~300) + reasoning (~200)
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


def generate_and_validate(topic: str) -> TweetGeneration:
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

    Returns:
        TweetGeneration with adaptive variant in 'long' field (short/mid are empty/invalid)
    """
    logger.info(f"[Adaptive Strategy] Generating tweet for topic: {topic[:100]}...")

    # === STEP 1: Generate adaptive variant ===
    logger.info("Step 1: Generating adaptive variant (140-270 chars, optimal length)...")
    tweet_text = generate_adaptive_variant(topic, attempt=1)

    if not tweet_text:
        logger.error("Failed to generate adaptive variant")
        return TweetGeneration(
            short=TweetVariant("", "short", False, 0, failure_reason="Not generated (single adaptive strategy)"),
            mid=TweetVariant("", "mid", False, 0, failure_reason="Not generated (single adaptive strategy)"),
            long=TweetVariant("", "long", False, 0, failure_reason="Generation failed"),
        )

    # === STEP 2: Validate adaptive variant ===
    logger.info(f"Step 2: Validating adaptive variant ({len(tweet_text)} chars)...")

    # Sanity check
    valid, reason = basic_sanity_check(tweet_text)
    if not valid:
        logger.error(f"Adaptive variant failed sanity check: {reason}")
        return TweetGeneration(
            short=TweetVariant("", "short", False, 0, failure_reason="Not generated (single adaptive strategy)"),
            mid=TweetVariant("", "mid", False, 0, failure_reason="Not generated (single adaptive strategy)"),
            long=TweetVariant(tweet_text, "long", False, len(tweet_text), failure_reason=reason),
        )

    # Length check (140-270)
    valid_length, length_reason = validate_length(tweet_text, "adaptive")
    if not valid_length:
        logger.warning(f"Adaptive variant length issue: {length_reason}")
        # Not a hard failure - we'll try refinement if contract fails

    # Contract validation
    validation = validate_against_contract(tweet_text, "adaptive")
    contract_passed = validation.get("cumple_contrato", False)

    # === STEP 3: Refinement if needed ===
    if not contract_passed:
        logger.warning(f"Adaptive variant failed contract validation: {validation.get('razonamiento', 'Unknown reason')}")
        logger.info("Step 3: Refining adaptive variant (attempt 2)...")

        # Refine once
        tweet_text_refined = generate_adaptive_variant(topic, attempt=2)

        if not tweet_text_refined:
            logger.error("Refinement failed: could not generate adaptive variant")
            return TweetGeneration(
                short=TweetVariant("", "short", False, 0, failure_reason="Not generated (single adaptive strategy)"),
                mid=TweetVariant("", "mid", False, 0, failure_reason="Not generated (single adaptive strategy)"),
                long=TweetVariant(tweet_text, "long", False, len(tweet_text),
                                failure_reason=validation.get("razonamiento", "Contract validation failed"),
                                validation_details=validation),
            )

        # Validate refined variant
        valid_refined, reason_refined = basic_sanity_check(tweet_text_refined)
        if not valid_refined:
            logger.error(f"Refined variant failed sanity check: {reason_refined}")
            return TweetGeneration(
                short=TweetVariant("", "short", False, 0, failure_reason="Not generated (single adaptive strategy)"),
                mid=TweetVariant("", "mid", False, 0, failure_reason="Not generated (single adaptive strategy)"),
                long=TweetVariant(tweet_text_refined, "long", False, len(tweet_text_refined), failure_reason=reason_refined),
            )

        validation_refined = validate_against_contract(tweet_text_refined, "adaptive")
        contract_passed_refined = validation_refined.get("cumple_contrato", False)

        if not contract_passed_refined:
            logger.error("❌ Refinement failed: Adaptive variant still does not pass contract after 2 attempts")
            failure_msg = f"Failed contract validation after refinement. Reason: {validation_refined.get('razonamiento', 'Unknown')}"
            return TweetGeneration(
                short=TweetVariant("", "short", False, 0, failure_reason="Not generated (single adaptive strategy)"),
                mid=TweetVariant("", "mid", False, 0, failure_reason="Not generated (single adaptive strategy)"),
                long=TweetVariant(tweet_text_refined, "long", False, len(tweet_text_refined),
                                failure_reason=validation_refined.get("razonamiento"),
                                validation_details=validation_refined),
            )

        # Refinement succeeded!
        logger.info("✓ Refined adaptive variant passed contract validation")
        tweet_text = tweet_text_refined
        validation = validation_refined
    else:
        logger.info("✓ Adaptive variant passed contract validation on first attempt")

    # === STEP 4: Return result ===
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
        )
    )

    logger.info(f"[Adaptive Strategy] Generation complete: {len(tweet_text)} chars, valid={generation.long.valid}")
    return generation
