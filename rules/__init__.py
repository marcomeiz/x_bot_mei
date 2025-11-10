"""
Rules Module - SINGLE SOURCE OF TRUTH for Voice Contract

All content generation and validation rules are centralized here.
NO MORE HARDCODED RULES in variant_generators.py or elsewhere.

Usage:
    from rules import get_contract_text, validate_contract_compliance

    # For generation prompts
    contract = get_contract_text()

    # For validation
    result = validate_contract_compliance(tweet_text)
    if not result.passed:
        print("Issues:", result.issues)
"""

from rules.contract_loader import (
    load_voice_contract,
    get_contract_text,
    get_generation_prompt,
    get_validation_prompt,
    get_forbidden_words,
    get_forbidden_phrases,
    allows_commas,
    allows_em_dash,
    requires_contractions,
)

from rules.validators import (
    validate_contract_compliance,
    check_contractions_present,
    check_sentence_variety,
    check_whatsapp_test,
    check_specificity_test,
    get_validation_summary,
    validate_icp_fit,
    check_icp_fit,
    ValidationResult,
)

__all__ = [
    # Contract loaders
    "load_voice_contract",
    "get_contract_text",
    "get_generation_prompt",
    "get_validation_prompt",
    "get_forbidden_words",
    "get_forbidden_phrases",
    "allows_commas",
    "allows_em_dash",
    "requires_contractions",
    # Validators
    "validate_contract_compliance",
    "check_contractions_present",
    "check_sentence_variety",
    "check_whatsapp_test",
    "check_specificity_test",
    "get_validation_summary",
    "validate_icp_fit",
    "check_icp_fit",
    "ValidationResult",
]
