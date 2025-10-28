---
id: generation/all_variants
purpose: One-shot multi-variant tweet generation (short/mid/long)
inputs: [topic_abstract]
constraints:
  - json_only
  - under_280
  - no_commas
  - no_and_or
model_hints:
  temperature: 0.6
---
Task: Generate three tweet drafts from the topic abstract below.
- Use a contrarian low-probability angle as backbone (<15%).
- English only. Remove clichés and boardroom jargon.

Topic abstract:
{topic_abstract}

Output JSON ONLY in this schema:
{
  "short": "<=160 chars",
  "mid":   "~180–230 chars",
  "long":  "240–280 chars"
}

