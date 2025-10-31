---
id: generation/contrast_analysis
purpose: Narrative contrast evaluator prompt
inputs:
  - topic
  - rag_section
constraints:
  - json_only
---
Topic: {{topic}}
{{rag_section}}

1. Describe the mainstream narrative most creators repeat about this topic (≤160 chars).
2. Describe a contrarian/orthogonal narrative that a fractional COO should push (≤160 chars).
3. Decide which narrative hits the ICP harder and explain why in ≤160 chars.

Return strict JSON:
{
  "mainstream": "...",
  "contrarian": "...",
  "winner": "mainstream|contrarian",
  "reason": "..."
}
