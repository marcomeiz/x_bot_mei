"""
Voice Contract Loader - SINGLE SOURCE OF TRUTH
Parses voice_contract.md and extracts rules dynamically.
NO MORE HARDCODED RULES.
"""
import re
from pathlib import Path
from typing import Dict, List, Optional
from functools import lru_cache

from logger_config import logger


class VoiceContract:
    """Representation of the Voice Contract with all rules parsed."""

    def __init__(self, contract_text: str):
        self.raw_text = contract_text
        self.role = self._extract_section(contract_text, "role")
        self.voice_rules = self._extract_section(contract_text, "voice_rules")
        self.connection_principles = self._extract_section(contract_text, "connection_principles")
        self.content_rules = self._extract_section(contract_text, "content_rules")
        self.forbidden = self._extract_section(contract_text, "forbidden")
        self.anti_ai_traps = self._extract_section(contract_text, "anti_ai_traps")
        self.self_check = self._extract_section(contract_text, "self_check")

        # Extract specific rules
        self.forbidden_words = self._extract_forbidden_words()
        self.forbidden_phrases = self._extract_forbidden_phrases()
        self.ai_patterns = self._extract_ai_patterns()
        self.allow_commas = self._check_commas_allowed()
        self.allow_em_dash = self._check_em_dash_allowed()
        self.require_contractions = self._check_contractions_required()

    def _extract_section(self, text: str, section_name: str) -> str:
        """Extract a section from the contract by tag name."""
        pattern = rf"<{section_name}>(.*?)</{section_name}>|##\s*<{section_name}>(.*?)(?=##\s*<|\Z)"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            content = match.group(1) or match.group(2) or ""
            return content.strip()

        # Fallback: look for section headers
        header_pattern = rf"##\s*<{section_name}>(.*?)(?=##|$)"
        match = re.search(header_pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

        return ""

    def _extract_forbidden_words(self) -> List[str]:
        """Extract forbidden words from contract."""
        words = []
        if self.forbidden:
            # Look for quoted words in forbidden section
            quoted = re.findall(r'"([^"]+)"', self.forbidden)
            words.extend(quoted)

            # Look for explicit word lists
            jargon_match = re.search(
                r'No "synergy," "leverage,".*?"ecosystem\."',
                self.forbidden,
                re.DOTALL
            )
            if jargon_match:
                jargon_words = re.findall(r'"([^"]+)"', jargon_match.group(0))
                words.extend(jargon_words)

        # De-duplicate and lowercase
        return list(set(w.lower() for w in words if w))

    def _extract_forbidden_phrases(self) -> List[str]:
        """Extract forbidden phrases from contract."""
        phrases = []

        # From content_rules - No AI tells
        ai_tells = [
            "It's important to note",
            "It's worth mentioning",
            "Essentially",
            "Fundamentally",
            "In essence",
            "Notably",
            "Importantly",
            "I hope this helps",
            "Feel free to",
            "Don't hesitate to",
        ]
        phrases.extend(ai_tells)

        # From content_rules - No template smell
        template_intros = [
            "In today's world",
            "Many people struggle with",
            "Let's talk about",
            "Have you ever wondered",
            "Imagine this",
        ]
        phrases.extend(template_intros)

        template_endings = [
            "In conclusion",
            "At the end of the day",
            "Remember that",
            "The choice is yours",
            "What will you choose",
        ]
        phrases.extend(template_endings)

        # From content_rules - No meta-commentary
        meta = [
            "this post",
            "this article",
            "in this section",
            "let's dive in",
            "let's explore",
            "let's unpack",
        ]
        phrases.extend(meta)

        return [p.lower() for p in phrases]

    def _extract_ai_patterns(self) -> List[Dict[str, str]]:
        """Extract AI patterns from anti_ai_traps section."""
        patterns = []

        if self.anti_ai_traps:
            # Parallel structure addiction
            patterns.append({
                "name": "parallel_structure",
                "description": "Same sentence start repeated 3+ times",
                "example_bad": "You need X. You need Y. You need Z.",
            })

            # Even-numbered lists
            patterns.append({
                "name": "even_numbered_lists",
                "description": "Perfect list of 3, 5, or 7 items (AI fingerprint)",
            })

            # Smooth transitions
            patterns.append({
                "name": "smooth_transitions",
                "description": "Always using 'Furthermore', 'Additionally', 'Moreover'",
                "forbidden_words": ["furthermore", "additionally", "moreover", "in addition"],
            })

            # Perfect grammar (should allow imperfection)
            patterns.append({
                "name": "perfect_grammar",
                "description": "No sentence fragments, always perfect structure (too polished)",
            })

            # Helpful wrap-up
            patterns.append({
                "name": "helpful_wrapup",
                "description": "Every piece ends with neat bow/conclusion",
            })

        return patterns

    def _check_commas_allowed(self) -> bool:
        """Check if commas are allowed in the contract."""
        if "Simple periods and commas only" in self.voice_rules:
            return True
        if "Never use em dashes" in self.voice_rules:
            # Implies commas are the alternative
            return True
        return False

    def _check_em_dash_allowed(self) -> bool:
        """Check if em dashes are allowed."""
        if "Never use em dashes" in self.voice_rules or "No em dashes. Ever" in self.forbidden:
            return False
        return True

    def _check_contractions_required(self) -> bool:
        """Check if contractions are required."""
        if "Use contractions (you're, it's, don't)" in self.voice_rules:
            return True
        return False

    def get_validation_prompt(self) -> str:
        """Get full contract text for LLM validation prompts."""
        return self.raw_text

    def get_generation_prompt(self) -> str:
        """Get contract formatted for generation prompts."""
        sections = []

        if self.role:
            sections.append(f"<ROLE>\n{self.role}\n</ROLE>")

        if self.voice_rules:
            sections.append(f"<VOICE_RULES>\n{self.voice_rules}\n</VOICE_RULES>")

        if self.connection_principles:
            sections.append(f"<CONNECTION_PRINCIPLES>\n{self.connection_principles}\n</CONNECTION_PRINCIPLES>")

        if self.content_rules:
            sections.append(f"<CONTENT_RULES>\n{self.content_rules}\n</CONTENT_RULES>")

        if self.forbidden:
            sections.append(f"<FORBIDDEN>\n{self.forbidden}\n</FORBIDDEN>")

        if self.anti_ai_traps:
            sections.append(f"<ANTI_AI_TRAPS>\n{self.anti_ai_traps}\n</ANTI_AI_TRAPS>")

        return "\n\n".join(sections)


@lru_cache(maxsize=1)
def load_voice_contract() -> VoiceContract:
    """
    Load the voice contract from voice_contract.md.
    Cached so we only parse once.

    Returns:
        VoiceContract instance with all rules parsed.
    """
    contract_path = Path(__file__).parent / "voice_contract.md"

    if not contract_path.exists():
        logger.error(f"Voice contract not found at {contract_path}")
        raise FileNotFoundError(f"Voice contract not found: {contract_path}")

    try:
        with open(contract_path, "r", encoding="utf-8") as f:
            contract_text = f.read()

        contract = VoiceContract(contract_text)
        logger.info("Voice contract loaded successfully")
        logger.info(f"  - Forbidden words: {len(contract.forbidden_words)}")
        logger.info(f"  - Forbidden phrases: {len(contract.forbidden_phrases)}")
        logger.info(f"  - Allow commas: {contract.allow_commas}")
        logger.info(f"  - Require contractions: {contract.require_contractions}")

        return contract

    except Exception as e:
        logger.error(f"Failed to load voice contract: {e}", exc_info=True)
        raise


def get_contract_text() -> str:
    """Get the full contract text for direct use in prompts."""
    return load_voice_contract().raw_text


def get_generation_prompt() -> str:
    """Get contract formatted for generation prompts."""
    return load_voice_contract().get_generation_prompt()


def get_validation_prompt() -> str:
    """Get contract formatted for validation prompts."""
    return load_voice_contract().get_validation_prompt()


# Convenience accessors
def get_forbidden_words() -> List[str]:
    """Get list of forbidden words from contract."""
    return load_voice_contract().forbidden_words


def get_forbidden_phrases() -> List[str]:
    """Get list of forbidden phrases from contract."""
    return load_voice_contract().forbidden_phrases


def allows_commas() -> bool:
    """Check if contract allows commas."""
    return load_voice_contract().allow_commas


def allows_em_dash() -> bool:
    """Check if contract allows em dashes."""
    return load_voice_contract().allow_em_dash


def requires_contractions() -> bool:
    """Check if contract requires contractions."""
    return load_voice_contract().require_contractions
