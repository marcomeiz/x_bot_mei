"""
Contract-Based Validators - NO HARDCODED RULES
All validation logic derives from voice_contract.md (SINGLE SOURCE OF TRUTH).
"""
import re
from typing import List, Tuple, Optional
from dataclasses import dataclass

from rules.contract_loader import (
    load_voice_contract,
    get_forbidden_words,
    get_forbidden_phrases,
    allows_commas,
    allows_em_dash,
    requires_contractions,
)
from logger_config import logger


@dataclass
class ValidationResult:
    """Result of a validation check."""
    passed: bool
    issues: List[str]
    warnings: List[str]


# Compiled regexes (for performance)
_WORD_REGEX = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")
_EMOJI_RE = re.compile(
    r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\u2600-\u26FF\u2700-\u27BF]",
    re.UNICODE,
)
_HASHTAG_RE = re.compile(r"#[A-Za-z0-9_]+")
_URL_RE = re.compile(r"(https?://\S+|\bwww\.[^\s]+)", re.IGNORECASE)
_CONTRACTION_PATTERNS = [
    r"\byou're\b", r"\bit's\b", r"\bdon't\b", r"\bcan't\b",
    r"\bwon't\b", r"\bdidn't\b", r"\bhasn't\b", r"\bhaven't\b",
    r"\bisn't\b", r"\baren't\b", r"\bwasn't\b", r"\bweren't\b",
    r"\bI'm\b", r"\bwe're\b", r"\bthey're\b"
]


def validate_contract_compliance(text: str, strict: bool = True) -> ValidationResult:
    """
    Validate text against the Voice Contract.

    This is the MAIN validator - all rules come from the contract.

    Args:
        text: Text to validate
        strict: If True, fails on any issue. If False, returns warnings.

    Returns:
        ValidationResult with passed status and list of issues/warnings
    """
    issues = []
    warnings = []

    if not text or not text.strip():
        return ValidationResult(passed=False, issues=["Empty text"], warnings=[])

    text_lower = text.lower()

    # 1. Check forbidden words (from contract)
    forbidden_words = get_forbidden_words()
    tokens = _WORD_REGEX.findall(text_lower)

    for word in forbidden_words:
        if word.lower() in tokens:
            issues.append(f"Contains forbidden word: '{word}'")

    # 2. Check forbidden phrases (from contract)
    forbidden_phrases = get_forbidden_phrases()
    for phrase in forbidden_phrases:
        if phrase.lower() in text_lower:
            issues.append(f"Contains forbidden phrase: '{phrase}'")

    # 3. Check em dashes (contract says "Never use em dashes. Ever.")
    if not allows_em_dash():
        if "—" in text or "–" in text:
            issues.append("Contains em dash (forbidden by contract)")

    # 4. Check commas (contract allows them for natural cadence)
    if "," in text and not allows_commas():
        issues.append("Contains commas (not allowed by current contract)")

    # 5. Check for contractions (contract requires them for natural speech)
    if requires_contractions():
        has_contraction = any(re.search(pattern, text, re.IGNORECASE) for pattern in _CONTRACTION_PATTERNS)

        # Also check for non-contracted forms that should be contracted
        non_contracted = [
            (r"\byou are\b", "you're"),
            (r"\bit is\b", "it's"),
            (r"\bdo not\b", "don't"),
            (r"\bI am\b", "I'm"),
        ]

        for pattern, should_be in non_contracted:
            if re.search(pattern, text, re.IGNORECASE):
                warnings.append(f"Found '{pattern}' - should use contraction '{should_be}' for natural speech")

    # 6. Technical checks (emojis, hashtags, URLs - always forbidden)
    if _EMOJI_RE.search(text):
        issues.append("Contains emoji")

    if _HASHTAG_RE.search(text):
        issues.append("Contains hashtag")

    if _URL_RE.search(text):
        issues.append("Contains URL")

    # 7. Check for AI patterns (from anti_ai_traps)
    ai_issues = _check_ai_patterns(text)
    if ai_issues:
        if strict:
            issues.extend(ai_issues)
        else:
            warnings.extend(ai_issues)

    # 8. Check for parallel structure (AI fingerprint)
    parallel_issues = _check_parallel_structure(text)
    if parallel_issues:
        warnings.extend(parallel_issues)

    # Determine pass/fail
    passed = len(issues) == 0

    return ValidationResult(passed=passed, issues=issues, warnings=warnings)


def _check_ai_patterns(text: str) -> List[str]:
    """Check for AI-specific patterns from contract's anti_ai_traps."""
    issues = []
    text_lower = text.lower()

    # Smooth transitions (AI always bridges)
    smooth_transitions = ["furthermore", "additionally", "moreover", "in addition"]
    for word in smooth_transitions:
        if f" {word} " in f" {text_lower} ":
            issues.append(f"AI pattern detected: smooth transition '{word}' (humans jump, not bridge)")

    # Symmetrical structures
    symmetrical = [
        r"not only .+ but also",
        r"on one hand .+ on the other hand",
    ]
    for pattern in symmetrical:
        if re.search(pattern, text_lower):
            issues.append(f"AI pattern: symmetrical structure (too polished)")

    # False questions (rhetorical Q immediately answered)
    if re.search(r"\?[^?]{0,50}(it's|the answer is|here's)", text_lower):
        issues.append("AI pattern: false question (asks then immediately answers)")

    return issues


def _check_parallel_structure(text: str) -> List[str]:
    """Check for parallel structure addiction (AI fingerprint)."""
    warnings = []

    sentences = _SENTENCE_SPLIT_REGEX.split(text.strip())
    if len(sentences) < 3:
        return warnings

    # Check first 3 words of each sentence
    first_words = []
    for sent in sentences:
        words = sent.strip().split()[:3]
        if words:
            first_words.append(" ".join(words).lower())

    # If 3+ sentences start the same way, that's parallel structure
    if len(first_words) >= 3:
        for i in range(len(first_words) - 2):
            if first_words[i] == first_words[i+1] == first_words[i+2]:
                warnings.append(
                    f"Parallel structure detected: 3+ sentences start with '{first_words[i]}' "
                    "(AI fingerprint - break the pattern)"
                )
                break

    return warnings


def check_contractions_present(text: str) -> bool:
    """
    Check if text contains contractions (required by contract for natural speech).

    Returns:
        True if contractions found, False otherwise
    """
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _CONTRACTION_PATTERNS)


def check_sentence_variety(text: str) -> Tuple[bool, str]:
    """
    Check for variable rhythm (forced asymmetry from contract).

    Contract says: "Mix short punches with longer thoughts. Deliberately break patterns."

    Returns:
        (is_varied, reason)
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_REGEX.split(text.strip()) if s.strip()]

    if len(sentences) < 2:
        return True, ""  # Can't check variety with < 2 sentences

    # Count words per sentence
    word_counts = [len(sent.split()) for sent in sentences]

    # Check if all sentences are same length (±1 word)
    if len(set(word_counts)) == 1:
        return False, "All sentences same length (no rhythm variation)"

    # Check for perfect staircase (AI loves this)
    is_ascending = all(word_counts[i] < word_counts[i+1] for i in range(len(word_counts)-1))
    is_descending = all(word_counts[i] > word_counts[i+1] for i in range(len(word_counts)-1))

    if is_ascending or is_descending:
        return False, "Perfect ascending/descending pattern (too structured, break it)"

    return True, ""


def check_whatsapp_test(text: str) -> Tuple[bool, str]:
    """
    WhatsApp Test from contract's self_check section.

    "Would I send this as-is to a smart friend who's stuck with this exact problem?
    If it feels stiff, over-produced, or like marketing copy → rewrite."

    This is a heuristic check for corporate/stiff language.

    Returns:
        (passes, reason)
    """
    text_lower = text.lower()

    # Corporate language indicators
    corporate_indicators = [
        "we are pleased to", "we would like to", "it is important",
        "we believe that", "our solution", "our platform",
        "industry-leading", "best-in-class", "world-class"
    ]

    for indicator in corporate_indicators:
        if indicator in text_lower:
            return False, f"Feels corporate/stiff: found '{indicator}'"

    # Over-produced indicators (too polished)
    if text.count(",") > len(text) / 50:  # More than 1 comma per 50 chars = over-punctuated
        return False, "Over-punctuated (too many commas for casual speech)"

    # Marketing copy indicators
    marketing = ["exclusive offer", "limited time", "act now", "don't miss"]
    for phrase in marketing:
        if phrase in text_lower:
            return False, f"Sounds like marketing: '{phrase}'"

    return True, ""


def check_specificity_test(text: str) -> Tuple[bool, str]:
    """
    Specificity Test from contract's self_check section.

    "Are there at least 2–3 concrete, lived-in details
    (numbers, scenes, phrases, names) instead of vague claims?"

    Returns:
        (has_specificity, reason)
    """
    # Count numbers
    numbers = re.findall(r'\b\d+(?:,\d{3})*(?:\.\d+)?\b', text)

    # Count specific time references
    time_refs = re.findall(
        r'\b(yesterday|today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
        r'morning|afternoon|evening|night|\d+\s*(am|pm|hours?|minutes?|days?|weeks?|months?|years?))\b',
        text.lower()
    )

    # Count specific places/names (capitalized words that aren't sentence starts)
    sentences = text.split('.')
    capitalized = []
    for sent in sentences:
        words = sent.split()[1:]  # Skip first word (sentence start)
        capitalized.extend([w for w in words if w and w[0].isupper()])

    # Total specificity markers
    specificity_count = len(numbers) + len(time_refs) + len(capitalized)

    if specificity_count < 2:
        return False, f"Only {specificity_count} concrete details (need ≥2 numbers/times/names)"

    return True, f"Found {specificity_count} concrete details"


def get_validation_summary(text: str) -> str:
    """
    Get a human-readable validation summary based on contract.

    Returns:
        Summary string with pass/fail for each check
    """
    result = validate_contract_compliance(text, strict=False)

    lines = ["Voice Contract Validation Summary:"]
    lines.append(f"  Overall: {'✅ PASS' if result.passed else '❌ FAIL'}")

    if result.issues:
        lines.append("\n  Issues:")
        for issue in result.issues:
            lines.append(f"    - {issue}")

    if result.warnings:
        lines.append("\n  Warnings:")
        for warning in result.warnings:
            lines.append(f"    - {warning}")

    # Self-check tests
    has_contractions = check_contractions_present(text)
    lines.append(f"\n  Contractions present: {'✅' if has_contractions else '❌'}")

    varied, variety_reason = check_sentence_variety(text)
    lines.append(f"  Sentence variety: {'✅' if varied else '❌'} {variety_reason}")

    whatsapp, whatsapp_reason = check_whatsapp_test(text)
    lines.append(f"  WhatsApp test: {'✅' if whatsapp else '❌'} {whatsapp_reason}")

    specific, specific_reason = check_specificity_test(text)
    lines.append(f"  Specificity test: {'✅' if specific else '❌'} {specific_reason}")

    return "\n".join(lines)
