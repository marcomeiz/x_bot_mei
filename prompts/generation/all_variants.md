---
id: generation/all_variants
purpose: Single-call multi-variant generation with strict guardrails (v2.0)
inputs: [topic_abstract]
constraints:
  - json_only
  - single_call
  - english_only
  - one_sentence_per_line
  - words_per_line_5_12
  - no_commas
  - no_and_or
  - char_ranges_strict
  - contrarian_backbone
  - actionable_for_ICP
  - avoid_cliches_hype_boardroom
  - no_hashtags_no_emojis
  - self_check_before_output
model_hints:
  temperature: 0.6
---
Task
You will write three tweet drafts from the topic abstract.
Write clean at the source. Do not rely on downstream fixes.

Voice & Audience
- VOICE CONTRACT V3 — Brutal Honest Solopreneur Ops.
- Solopreneur-in-the-Trenches ICP (operators-first, tactical).

Hard Rules (must all pass)
- English only. No Spanish words or diacritics.
- One sentence per line. Each line ends with . ! or ?
- 5–12 words per line.
- No commas. If you need a pause, split into a new line.
- Do not use conjunctions: and/or (or y/o). Use separate sentences.
- No emojis, no hashtags, no links.
- No hedging, clichés, or hype words.
- Clear micro-win (tactical next step) evident in the body.
- Character ranges by variant:
  - short ≤ 160
  - mid 180–230
  - long 240–280
- Close with a single inspirational hammer line (decisive, not fluffy).

Topic abstract
{topic_abstract}

Self-check before output
- If any rule is violated, fix and re-write the draft before returning.

Output
Return JSON ONLY with this schema (line breaks allowed inside strings):
{
  "short": "...",
  "mid": "...",
  "long": "..."
}
Do not include any explanation, headings, or comments outside the JSON.
