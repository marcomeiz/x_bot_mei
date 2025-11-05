---
id: generation/all_variants_v4
purpose: One-shot multi-variant generation (V4 - Clean Arch)
inputs: [topic_abstract, gold_examples_block]
constraints: [json_only]
model_hints:
  temperature: 0.6
---
Task:
You will write three tweet drafts based *only* on the Topic provided.
You MUST adhere strictly to the <STYLE_CONTRACT> and <ICP> provided in the System Prompt.

Topic:
{topic_abstract}

Reference Voice Anchors (Full Text):
{gold_examples_block}

Output:
Return JSON ONLY with this schema:
{
  "short": "...",
  "mid": "...",
  "long": "..."
}
