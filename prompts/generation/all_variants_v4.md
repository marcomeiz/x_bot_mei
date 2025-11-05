---
id: generation/all_variants_v4
purpose: One-shot multi-variant generation (V4 - Clean Arch)
inputs:
  - topic_abstract
  - gold_examples_block
  - len_short_max
  - len_mid_min
  - len_mid_max
  - len_long_min
  - len_long_max
constraints: [json_only]
model_hints:
  temperature: 0.6
---
**Prime Directive: Clarity of Diagnosis > Poetic Creativity.**
Your absolute priority is to be understood in 3 seconds. The goal is to provide a sharp, operational diagnosis, not a philosophical musing.

**Metaphor & Analogy Rule: Concrete & Drawable ONLY.**
- Any metaphor MUST be 100% concrete and visual (physical objects, tangible actions).
- Any abstract or philosophical metaphor (e.g., "the grief of a fantasy," "sacrificing control") is a failure.
- If you are in doubt, ALWAYS default to a literal, direct statement.

Your task is to generate three distinct, high-quality tweet variants based on the provided topic, each with a different length and structural feel. Follow these steps internally:

1.  **Analyze the Topic:**
    -   Topic: "{topic_abstract}"
    -   Reference Voice Anchors (Full Text):
        {gold_examples_block}

2.  **Internal Brainstorm (Chain of Thought):**
    -   Generate 1-2 contrarian or non-obvious angles for this topic.
    -   Select the strongest angle to use as the core theme for all three versions.

3.  **Drafting (Internal Thought):**
    -   **Version A (The Surgical Diagnosis):** Write a 1-2 line knockout blow (≤{len_short_max} characters). It must NOT be a vague positive statement. It MUST be a brutal, specific operational or financial diagnosis that attacks the ICP's failed math, false identity, or broken system. (Example: 'Stop being the highest-paid 0/hr employee in your own business.')
    -   **Version B (Standard):** Write a standard-length draft ({len_mid_min}–{len_mid_max} characters) with a solid rhythm.
    -   **Version C (Extended):** Write a longer draft ({len_long_min}–{len_long_max} characters) that tells a mini-story or ends with a strong, imperative call to action.
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