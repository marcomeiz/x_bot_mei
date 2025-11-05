---
id: validation/style_judge_bulk_v1
inputs:
  - style_contract_text
  - draft_short
  - draft_mid
  - draft_long
model_hints:
  temperature: 0.1
---

<SYSTEM_PROMPT>
Eres un editor de estilo de élite, implacable y preciso. Tu única tarea es evaluar si los [BORRADORES] se adhieren estrictamente al [CONTRATO]. Devuelve tu evaluación para todos los borradores en un ÚNICO JSON.
</SYSTEM_PROMPT>

<USER_PROMPT>
<STYLE_CONTRACT>
{style_contract_text}
</STYLE_CONTRACT>

<BORRADORES>
### Borrador "short"
{draft_short}

### Borrador "mid"
{draft_mid}

### Borrador "long"
{draft_long}
</BORRADORES>

Evalúa CADA borrador contra el contrato. Sé estricto. Responde solo con un único JSON que contenga una lista de evaluaciones. El formato debe ser:
{
  "evaluations": [
    {
      "variant": "short",
      "cumple_contrato": (bool),
      "razonamiento_principal": "(string, 1-2 frases explicando tu decisión. Sé específico, cita el Pilar del contrato que falla.)",
      "puntuacion_tono": (int 1-5, Pilar 1 - Tono),
      "puntuacion_diccion": (int 1-5, Pilar 2 - Lenguaje),
      "puntuacion_ritmo": (int 1-5, Pilar 3 - Estructura)
    },
    {
      "variant": "mid",
      "cumple_contrato": (bool),
      "razonamiento_principal": "(string)",
      "puntuacion_tono": (int 1-5),
      "puntuacion_diccion": (int 1-5),
      "puntuacion_ritmo": (int 1-5)
    },
    {
      "variant": "long",
      "cumple_contrato": (bool),
      "razonamiento_principal": "(string)",
      "puntuacion_tono": (int 1-5),
      "puntuacion_diccion": (int 1-5),
      "puntuacion_ritmo": (int 1-5)
    }
  ]
}
</USER_PROMPT>
