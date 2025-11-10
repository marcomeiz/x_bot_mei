# Migration Guide: From Hardcoded Rules to Centralized Voice Contract

## 🎯 OBJETIVO

Eliminar TODAS las reglas hardcodeadas dispersas en el código y usar `rules/voice_contract.md` como **SINGLE SOURCE OF TRUTH**.

---

## ✅ QUÉ SE CREÓ

### Nueva estructura:
```
rules/
├── __init__.py              # API pública del módulo
├── voice_contract.md        # SINGLE SOURCE OF TRUTH (tu prompt)
├── contract_loader.py       # Parser del contract
└── validators.py            # Validadores basados en contract
```

---

## 🔥 ARCHIVOS A DEPRECAR/REFACTORIZAR

### 1. `config/warden.yaml` - **ELIMINAR**
```yaml
# ❌ ANTES (hardcodeado en YAML)
comment_guardrails:
  enforce_no_commas: true      # CONTRADICE el contract
  enforce_no_and_or: true
  forbidden_em_dash: "—"
```

**SOLUCIÓN:** Toda esta config ya está en `voice_contract.md`. Eliminar archivo.

---

### 2. `variant_generators.py` - **REFACTORIZAR**

#### ❌ ANTES (líneas 104-207):
```python
# Hardcoded config
DEFAULT_WARDEN_GUARDRAILS = {
    "enforce_no_commas": True,
    "words_per_line": {"min": 5, "max": 12},
    "mid_chars": {"min": 180, "max": 230},
}

BANNED_WORDS = get_banned_words()  # From lexicon
ENFORCE_NO_COMMAS = os.getenv("ENFORCE_NO_COMMAS", True)
```

#### ✅ DESPUÉS:
```python
from rules import (
    get_contract_text,
    allows_commas,
    get_forbidden_words,
    validate_contract_compliance
)

# No hardcoded config - everything from contract
contract_text = get_contract_text()
forbidden_words = get_forbidden_words()  # From contract, not lexicon
ENFORCE_NO_COMMAS = not allows_commas()  # From contract
```

---

### 3. `simple_generator.py` - **REFACTORIZAR**

#### ❌ ANTES (líneas 365-444):
```python
# 120 líneas de prompt inline hardcodeado
prompt = f"""Generate ONE tweet about this topic.

<ROLE>
You're an experienced operator and writer...
</ROLE>

<VOICE_RULES>
• Always second person...
• Natural speech...
</VOICE_RULES>

# ... 100 más líneas de reglas hardcodeadas ...
"""
```

#### ✅ DESPUÉS:
```python
from rules import get_generation_prompt

# Contract as prompt (SINGLE SOURCE OF TRUTH)
contract = get_generation_prompt()

prompt = f"""Generate ONE tweet about this topic.

{contract}

<TOPIC>
{topic}
</TOPIC>

⚠️ ADAPTIVE LENGTH REQUIREMENT:
- Range: 140-270 characters
- Choose optimal length for topic
- Prioritize COMPLETENESS
"""
```

**REDUCCIÓN: De 120 líneas de prompt hardcodeado a 10 líneas dinámicas.**

---

### 4. `writing_rules.py` - **REFACTORIZAR O ELIMINAR**

#### ❌ ANTES:
```python
# Hardcoded banned words
BANNED_WORDS = get_banned_words()  # From lexicon.py

# Hardcoded regex patterns
_AI_PATTERN_STRINGS = (
    "most people",
    "in today's",
    "let's dive",
    # ... etc
)

def words_blocklist_prompt() -> str:
    return "- Forbidden words: " + ", ".join(sorted(BANNED_WORDS))
```

#### ✅ DESPUÉS:
```python
from rules import get_forbidden_words, get_forbidden_phrases

# Everything from contract
BANNED_WORDS = get_forbidden_words()  # From contract
BANNED_PHRASES = get_forbidden_phrases()  # From contract

def words_blocklist_prompt() -> str:
    words = get_forbidden_words()
    phrases = get_forbidden_phrases()
    return f"- Forbidden words: {', '.join(words)}\n- Forbidden phrases: {', '.join(phrases)}"
```

**O MEJOR: Eliminar este archivo completamente y usar `rules.validators` directamente.**

---

### 5. Validación - **UNIFICAR**

#### ❌ ANTES (disperso en múltiples archivos):
```python
# variant_generators.py:266-273
def _no_banned_language(text: str) -> Optional[str]:
    if HEDGING_REGEX.search(text):
        return "hedging"
    if CLICHE_REGEX.search(text):
        return "cliche"
    # ...

# simple_generator.py:558-581
def basic_sanity_check(text: str) -> Tuple[bool, str]:
    if not text.strip():
        return False, "Empty text"
    if re.search(r'emoji_pattern', text):
        return False, "Contains emoji"
    # ...
```

#### ✅ DESPUÉS (centralizado):
```python
from rules import validate_contract_compliance

# Una sola función para todo
result = validate_contract_compliance(tweet_text, strict=True)

if not result.passed:
    logger.error(f"Validation failed: {result.issues}")
    return None

# Opcional: mostrar warnings
if result.warnings:
    logger.warning(f"Warnings: {result.warnings}")
```

---

## 📝 EJEMPLO DE MIGRACIÓN COMPLETO

### File: `simple_generator.py` (REFACTORIZADO)

```python
"""
Simple tweet generator - follows ONLY the Voice Contract.
"""
from rules import (
    get_generation_prompt,
    validate_contract_compliance,
    get_validation_summary,
)

def generate_adaptive_variant(topic: str, attempt: int = 1) -> Tuple[str, Optional[Dict]]:
    """Generate a single adaptive-length tweet following the Voice Contract."""

    # ✅ Contract as prompt (SINGLE SOURCE)
    contract = get_generation_prompt()

    prompt = f"""{contract}

<TOPIC>
{topic}
</TOPIC>

⚠️ ADAPTIVE LENGTH: 140-270 chars, optimal for topic.

Return ONLY JSON: {{"tweet": "your tweet text here"}}"""

    try:
        response = llm.chat_json(
            model=settings.post_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )

        tweet = response.get("tweet", "").strip()

        # ✅ Validate using contract (SINGLE SOURCE)
        result = validate_contract_compliance(tweet, strict=False)

        if not result.passed:
            logger.warning(f"Generated tweet failed contract: {result.issues}")
            if attempt < 2:
                # Retry with feedback
                return generate_adaptive_variant(topic, attempt=2)

        logger.info(get_validation_summary(tweet))

        return tweet, usage_info

    except Exception as e:
        logger.error(f"Generation failed: {e}", exc_info=True)
        return "", None
```

**ANTES: 750 líneas con reglas dispersas.**
**DESPUÉS: ~100 líneas usando contract centralizado.**

---

## 🚀 PASOS DE MIGRACIÓN

### Fase 1: Preparación (DONE ✅)
- [x] Crear `rules/` module
- [x] Crear `voice_contract.md` (tu prompt)
- [x] Crear `contract_loader.py`
- [x] Crear `validators.py`

### Fase 2: Refactorizar Generadores
- [ ] Migrar `simple_generator.py` a usar `get_generation_prompt()`
- [ ] Migrar `variant_generators.py` a usar contract
- [ ] Eliminar prompts inline hardcodeados

### Fase 3: Refactorizar Validadores
- [ ] Reemplazar todas las llamadas a `detect_banned_elements()` con `validate_contract_compliance()`
- [ ] Eliminar regex hardcodeados duplicados
- [ ] Unificar validación en `rules.validators`

### Fase 4: Limpiar Config
- [ ] Eliminar `config/warden.yaml` (duplicado)
- [ ] Revisar `writing_rules.py` - deprecar o integrar en `rules/`
- [ ] Limpiar `variant_generators.py` de hardcodeo

### Fase 5: Testing
- [ ] Verificar que generación funciona con nuevo sistema
- [ ] Verificar que validación funciona
- [ ] Comparar outputs antes/después (deben ser mejores con nuevo contract)

---

## 🎯 BENEFICIOS INMEDIATOS

1. **Single Source of Truth**: Cambias reglas en UN lugar (`voice_contract.md`)
2. **No más contradicciones**: Contract permite commas, código ya no las prohíbe
3. **Menos código**: Eliminas 500+ líneas de reglas hardcodeadas
4. **Más mantenible**: Reglas en Markdown (legible), no en Python
5. **Más testable**: Validators independientes, fácil de testear
6. **Escalable**: Añadir nueva regla = editar Markdown, no tocar 5 archivos

---

## ⚠️ COMPATIBILIDAD

Durante la migración, ambos sistemas pueden coexistir:

```python
# Old way (deprecar gradualmente)
from writing_rules import detect_banned_elements

# New way (usar para todo código nuevo)
from rules import validate_contract_compliance
```

Pero el objetivo es **eliminar completamente** el código viejo.

---

## 📊 MÉTRICAS DE ÉXITO

Después de migración completa:

- [ ] 0 hardcoded rules en Python files
- [ ] 1 archivo con todas las reglas (`voice_contract.md`)
- [ ] < 100 líneas de validación (vs 500+ actual)
- [ ] < 50 líneas de prompts en generadores (vs 120+ actual)
- [ ] 100% validación basada en contract

---

**PRÓXIMO PASO:** ¿Quieres que refactorice `simple_generator.py` AHORA para mostrarte el resultado final?
