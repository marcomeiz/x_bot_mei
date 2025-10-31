---
id: generation/all_variants
purpose: One-shot multi-variant generation focused on VOICE only (V3.1)
inputs: [topic_abstract]
constraints:
  - json_only
  - voice_focus_only
model_hints:
  temperature: 0.6
---
Task
You will write three tweet drafts from the topic abstract.
We are fixing VOICE only. Do not rely on downstream fixes.

Voice & Audience (apply ONLY these cues)
- Brutal voice Hormozi V3.1.
- Conversational, street‑smart English.
- Inclusive but accountable tone (validate chaos, demand ownership).
- Start from pain + identity shift.
- No hype, no corporate jargon.
- Short, punchy lines.
- ASCII punctuation only; never use em dashes (—) or fancy separators.
No other mandates right now.

Session override (ignore in the contract for this task):
- Ignore “Micro‑wins”, CTAs, tactics or step‑by‑step advice.
- Ignore “Mandatory Rhythm Mix”.
Do NOT add goals or constraints beyond the cues above.

Topic
{topic_abstract}

Self‑check before output
- If any of the cues above are missing, rewrite to fix VOICE.

Output
Return JSON ONLY with this schema (line breaks allowed inside strings):
{
  "short": "...",
  "mid": "...",
  "long": "..."
}
Do not include any explanation, headings, or comments outside the JSON.
