# 🔴 CONTRADICCIONES CRÍTICAS DETECTADAS

**Fecha:** 2025-11-10
**Análisis de:** variant_generators.py, writing_rules.py, config/warden.yaml

---

## ⚠️ CONTRADICCIÓN #1: COMMAS (CRÍTICA PARA TONO)

### 📍 Ubicación:
- **Contract (SINGLE SOURCE OF TRUTH):** `rules/voice_contract.md` línea 25
- **Código actual:** `variant_generators.py` línea 190
- **Config vieja:** `config/warden.yaml` línea 2

### ❌ Estado Actual (CONTRADICTORIO):

```yaml
# config/warden.yaml
enforce_no_commas: true  # ← PROHÍBE commas
```

```python
# variant_generators.py:190
ENFORCE_NO_COMMAS = True  # ← PROHÍBE commas
```

```markdown
# rules/voice_contract.md
### Human hesitations (controlled)
- Never use em dashes. Simple periods and commas only.  # ← PERMITE commas
```

### ✅ Lo que dice el CONTRACT (CORRECTO):

> "Never use em dashes. **Simple periods and commas only.**"

**Interpretation:** Commas están PERMITIDAS para ritmo natural.

### 🔥 Impacto:

**ALTO - Afecta el tono natural.**

El código actual **rechaza** tweets con commas, pero el contract los **permite** para cadencia natural.

Ejemplo rechazado incorrectamente:
```
"You're not confused, you're avoiding the thing that scares you."
```
☝️ Este tweet sería RECHAZADO por tener coma, pero es PERFECTAMENTE válido según el contract.

### 🚨 Ubicaciones donde se valida:

```python
# variant_generators.py
Línea 332:  if not ENFORCE_NO_COMMAS:
Línea 1423: enforce_no_commas: bool = ENFORCE_NO_COMMAS
Línea 1497: if ENFORCE_NO_COMMAS and "," in pline:
Línea 1556: if ENFORCE_NO_COMMAS and "," in draft:
```

### 🛠️ Solución Recomendada:

```python
# Cambiar línea 190 en variant_generators.py
# ANTES:
ENFORCE_NO_COMMAS = True

# DESPUÉS:
from rules import allows_commas
ENFORCE_NO_COMMAS = not allows_commas()  # Respeta el contract
```

**NOTA:** Esto cambiará comportamiento. Antes rechazaba commas, ahora las permitirá.

---

## ⚠️ CONTRADICCIÓN #2: BANNED WORDS (SISTEMAS DESALINEADOS)

### 📍 Ubicación:
- **Contract:** `rules/voice_contract.md`
- **Lexicon:** `src/lexicon.py` → `config/lexicon.json`
- **Writing Rules:** `writing_rules.py` línea 144

### ❌ Estado Actual (DESALINEADO):

**Contract (14 palabras, inglés):**
```
critical, crucial, elevate, empower, essential, game-changer,
optimize, powerful, transform, unlock, leverage, etc.
```

**Lexicon (5 palabras, español):**
```
bien, bueno, entonces, solo, ya
```

**Overlap:** 0 palabras en común

### 🔥 Impacto:

**MEDIO - Sistemas independientes.**

Parece que:
- **Contract:** Para validación de contenido en inglés (AI tells, buzzwords)
- **Lexicon:** Para contenido en español (palabras de relleno)

### ❓ Pregunta Crítica:

¿El bot genera en inglés o español? Si genera en ambos, ambos sistemas son necesarios.

### 🛠️ Solución Recomendada:

**Opción 1 (si solo inglés):**
```python
from rules import get_forbidden_words
BANNED_WORDS = get_forbidden_words()  # Usar contract
```

**Opción 2 (si ambos idiomas):**
```python
from rules import get_forbidden_words
from src.lexicon import get_banned_words

BANNED_WORDS = set(get_forbidden_words()) | set(get_banned_words())  # Merge
```

---

## ⚠️ CONTRADICCIÓN #3: FORBIDDEN PHRASES (FALTANTES)

### 📍 Ubicación:
- **Contract:** `rules/voice_contract.md` - 26 frases prohibidas
- **Código actual:** No validación explícita de frases

### ❌ Estado Actual:

El contract tiene 26 frases prohibidas específicas para detectar AI tells:

```
"it's important to note"
"it's worth mentioning"
"essentially"
"in today's world"
"let's talk about"
"i hope this helps"
# ... 20 más
```

**Pero el código actual NO las valida.**

### 🔥 Impacto:

**MEDIO - AI tells pasan sin detectar.**

Frases como "It's important to note" deberían rechazarse pero no lo hacen.

### 🛠️ Solución Recomendada:

```python
# Añadir validación en variant_generators.py
from rules import get_forbidden_phrases

def _check_forbidden_phrases(text: str) -> List[str]:
    issues = []
    text_lower = text.lower()
    for phrase in get_forbidden_phrases():
        if phrase in text_lower:
            issues.append(f"Contains forbidden phrase: '{phrase}'")
    return issues
```

---

## ⚠️ CONTRADICCIÓN #4: HARDCODED PROMPTS

### 📍 Ubicación:
- **variant_generators.py** líneas 502-531: `REVIEWER_PROFILES`
- **Debería estar en:** `prompts/` directory o contract

### ❌ Estado Actual:

```python
# variant_generators.py:502-531 (30 líneas hardcodeadas)
REVIEWER_PROFILES: List[Dict[str, str]] = [
    {
        "name": "Contrarian Reviewer",
        "role": "You are obsessed with tail-distribution...",
        # ... más prompts hardcodeados
    },
]
```

### 🔥 Impacto:

**BAJO - Mantenibilidad.**

No afecta tono, pero dificulta mantener prompts.

### 🛠️ Solución Recomendada:

Mover a `prompts/reviewers/` y cargar dinámicamente:

```python
from src.prompt_loader import load_prompt

REVIEWER_PROFILES = [
    {
        "name": "Contrarian Reviewer",
        "role": load_prompt("reviewers/contrarian.txt"),
    },
]
```

---

## ⚠️ CONTRADICCIÓN #5: POST CATEGORIES (HARDCODED)

### 📍 Ubicación:
- **variant_generators.py** líneas 356-483: `DEFAULT_POST_CATEGORIES` (127 líneas)
- **config/post_categories.json** (mismo contenido)

### ❌ Estado Actual:

**DUPLICACIÓN:** Categorías definidas en 2 lugares:
1. Python (variant_generators.py)
2. JSON (config/post_categories.json)

### 🔥 Impacto:

**BAJO - Duplicación de datos.**

### 🛠️ Solución Recomendada:

```python
# Eliminar líneas 356-483 de variant_generators.py
# Cargar desde JSON:
import json
with open("config/post_categories.json") as f:
    POST_CATEGORIES = json.load(f)
```

---

## 📊 RESUMEN EJECUTIVO

| # | Contradicción | Severidad | Afecta Tono | Acción |
|---|---------------|-----------|-------------|--------|
| 1 | COMMAS prohibidas vs permitidas | 🔴 CRÍTICA | ✅ SÍ | Alinear con contract |
| 2 | BANNED_WORDS desalineados | 🟡 MEDIA | ⚠️ PARCIAL | Clarificar idiomas |
| 3 | FORBIDDEN_PHRASES no validadas | 🟡 MEDIA | ✅ SÍ | Añadir validación |
| 4 | REVIEWER_PROFILES hardcoded | 🟢 BAJA | ❌ NO | Mover a prompts/ |
| 5 | POST_CATEGORIES duplicados | 🟢 BAJA | ❌ NO | Usar solo JSON |

---

## 🚀 PLAN DE ACCIÓN RECOMENDADO

### Fase 1: CRÍTICO (Afecta Tono) ⚠️
1. ✅ **Alinear COMMAS con contract**
   - Cambiar `ENFORCE_NO_COMMAS` para respetar `allows_commas()`
   - **Riesgo:** Cambia validación (tweets con commas pasarán)
   - **Beneficio:** Tono más natural, alineado con contract

2. ✅ **Validar FORBIDDEN_PHRASES**
   - Añadir check para 26 frases AI tells
   - **Riesgo:** Bajo (solo añade validación)
   - **Beneficio:** Mejor detección de AI patterns

### Fase 2: Clarificación 🔍
3. ❓ **Clarificar idiomas (inglés vs español)**
   - Verificar qué idioma genera el bot
   - Decidir si merge banned words o mantener separado

### Fase 3: Cleanup 🧹
4. ✅ **Eliminar hardcodeo no crítico**
   - Mover REVIEWER_PROFILES a archivos
   - Usar POST_CATEGORIES desde JSON
   - **Riesgo:** Mínimo
   - **Beneficio:** Código más limpio

---

## ⚠️ DECISIÓN REQUERIDA

**Pregunta para el usuario:**

> ¿El bot genera tweets en **inglés** o **español** (o ambos)?

**Esto determina cómo manejamos banned words:**
- Solo inglés → Usar contract
- Solo español → Usar lexicon
- Ambos → Merge ambos sistemas

---

*Generado por análisis automático del codebase el 2025-11-10*
