from dataclasses import dataclass

from persona import (
    get_final_guidelines_text,
    get_icp_text,
    get_style_contract_text,
)


@dataclass(frozen=True)
class PromptContext:
    """Bundles the shared persona artefacts for prompt construction."""

    contract: str
    icp: str
    final_guidelines: str


def build_prompt_context() -> PromptContext:
    """Retrieve the latest persona artefacts with caching provided by persona.py."""
    return PromptContext(
        contract=get_style_contract_text(),
        icp=get_icp_text(),
        final_guidelines=get_final_guidelines_text(),
    )

