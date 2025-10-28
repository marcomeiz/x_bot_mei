---
id: generation/all_variants
purpose: Multi-variant tweet generation (short/mid/long)
inputs: [topic_abstract, intensity=med]
constraints:
  - json_only
  - english_only
  - one_sentence_per_line
  - contrarian_backbone
  - actionable_for_ICP
  - avoid_cliches_and_boardroom_jargon
  # stylistic toggles:
  - allow_no_commas: true        # si desactivamos, ajustar validadores
  - limit_conjunctions: 1_per_line
model_hints:
  temperature: 0.6
---
Task:
Generate three tweet drafts from the topic abstract below.
Each variant must:
- Apply Alex Hormozi voice contract (style + rhythm).
- Target Solopreneur-in-the-Trenches ICP.
- Provide a clear tactical next step (micro-win).
- Use whitespace to add weight (line breaks).
Topic abstract:
{topic_abstract}

Output JSON ONLY in this schema:
{
  "short": "<=160 chars",
  "mid": "180–230 chars",
  "long": "240–280 chars"
}
# Line breaks are allowed within each string.
# No explanation or commentary.
