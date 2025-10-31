---
id: generation/tail_sampling
purpose: Verbalized tail-sampling hooks prompt
inputs:
  - topic
  - tail_count
  - rag_section
constraints:
  - json_only
---
We are drafting content for a fractional COO persona. Use verbalized sampling to explore {{tail_count}} low-probability hooks (p < 0.15) about:

TOPIC: {{topic}}
{{rag_section}}

For each hook:
1. Identify the mainstream narrative you're challenging.
2. Summarize the contrarian/orthogonal insight (≤ 2 sentences).
3. Provide a probability string like "0.08" (must be < 0.15).
4. Explain briefly why this tail angle matters for an overwhelmed day 1–year 1 solopreneur.

Return JSON like:
{
  "angles": [
    {
      "probability": "0.09",
      "mainstream": "...",
      "angle": "...",
      "rationale": "..."
    }
  ]
}

Keep each field ≤ 180 characters.
