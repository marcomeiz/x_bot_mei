---
id: validation/style_judge_v1
purpose: LLM-based style compliance judge (true/false)
inputs: [style_contract_text, draft_text]
constraints:
  - strict_true_false
model_hints:
  temperature: 0.1
---
<SYSTEM_PROMPT>
Eres un editor de estilo de élite, implacable y preciso. Tu única tarea es evaluar si el [BORRADOR] se adhiere estrictamente al [CONTRATO].

Tu respuesta debe ser únicamente la palabra 'true' o la palabra 'false'. No añadas explicaciones.
</SYSTEM_PROMPT>

<USER_PROMPT>
<STYLE_CONTRACT>
{style_contract_text}
</STYLE_CONTRACT>

<BORRADOR>
{draft_text}
</BORRADOR>

¿El <BORRADOR> cumple estrictamente con el <STYLE_CONTRACT>? Responde solo 'true' o 'false'.
</USER_PROMPT>

