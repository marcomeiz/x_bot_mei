# Optimization Roadmap

**Fecha creación**: 2025-11-06
**Contexto**: Después de optimización que redujo /g de ~60s a ~30s eliminando código obsoleto.

## Stack Actual (Lo que está bien ✅)

1. **LLM Stack**: GPT-4/Claude con fallback automático - excelente
2. **Contract-first generation**: `simple_generator.py` (300 líneas, 2 LLM calls) - oro puro
3. **Embeddings + ChromaDB**: Similarity search para evitar repetir tweets - correcto
4. **Goldset reference**: Benchmarking contra tweets de Hormozi - smart
5. **Memory collection**: Tracking de lo publicado - necesario

---

## Mejoras Priorizadas

### 🔴 CRÍTICO (Deuda técnica que frena desarrollo)

#### 1. Refactorizar `proposal_service.py` (950+ líneas)
**Problema**: Viola SRP. Un archivo hace generación + aprobación + Telegram + embeddings + validación + métricas.

**Solución**:
```
services/
  ├── tweet_generator_service.py    # Solo generación (generate_tweet_from_topic)
  ├── approval_service.py           # Solo aprobaciones (_handle_approve, _finalize_choice)
  ├── telegram_service.py           # Solo envío de mensajes (send_message, format_proposal)
  ├── validation_service.py         # Solo validación de contrato (_check_contract_requirements)
  └── metrics_service.py            # Solo logging de métricas (log_post_metrics)
```

**Impacto**:
- 10x más testeable
- 5x más fácil de mantener
- 3x más rápido de debuggear

**Estimación**: 2 días de refactor

---

#### 2. Eliminar código muerto
**Problema**: Archivos obsoletos que confunden y generan deuda técnica.

**Archivos a mover a `/legacy` o eliminar**:
- `evaluation.py` - Obsoleto desde commit 8d74d7c (solo se usa en comments)
- `variant_generators.py` - 1200 líneas obsoletas (reemplazado por simple_generator.py)
- `writing_rules.py` - 325 líneas de reglas hardcoded (reemplazadas por contrato)
- `config/warden.yaml` - Validación obsoleta
- `config/evaluation_fast.yaml` - Obsoleto
- `config/evaluation_slow.yaml` - Obsoleto
- `config/lexicon.json` - Palabras baneadas que el contrato no menciona

**Impacto**: Reduce confusión, clarifica qué código está activo

**Estimación**: 2 horas

---

#### 3. Unificar sistema de mensajes
**Problema**: Duplicación estúpida de mensajes.
```
config/messages.yaml    # ← 50 líneas
src/messages.py         # ← Las MISMAS 50 líneas
```

**Solución**: Elegir UNO:
- Opción A: Solo YAML (mejor si vas a tener i18n)
- Opción B: Solo Python dict (más simple si solo es español)

**Impacto**: Single source of truth para mensajes

**Estimación**: 1 hora

---

### 🟡 IMPORTANTE (Mejoras de performance/observabilidad)

#### 4. Downgrade embeddings a `text-embedding-3-small`
**Problema**: Usas `text-embedding-3-large` (3072 dims) para tweets de 280 chars. Overkill.

**Solución**:
```python
# Cambiar en embeddings_manager.py
EMBEDDING_MODEL = "text-embedding-3-small"  # 1536 dims
```

**Impacto**:
- 50% menos costo
- 40% más rápido
- ~5% pérdida de precisión (aceptable para tweets)

**Estimación**: 30 minutos + reembed collections

---

#### 5. Tunear ChromaDB HNSW parameters
**Problema**: Usando defaults que no están optimizados para tu caso.

**Solución**:
```python
collection = client.create_collection(
    name="topics",
    metadata={
        "hnsw:ef_construction": 200,  # Default=100, muy bajo
        "hnsw:ef_search": 100,        # Default=10, muy bajo
        "hnsw:M": 16,                 # Default OK
    }
)
```

**Impacto**: 30-50% más rápido en queries de similaridad

**Estimación**: 1 hora + rebuild collections

---

#### 6. Observabilidad seria
**Problema**: Tienes `Timer`, `logger`, `diagnostics_logger` dispersos pero sin dashboard ni alertas.

**Solución**:
- Implementar OpenTelemetry o Cloud Monitoring estructurado
- Dashboard en GCP con:
  - P50/P95/P99 de tiempo de generación
  - Costo por tweet generado
  - Rate de aprobación por variante (A/B/C)
  - Tasa de éxito de generación
- Alertas si P95 > 60s o error rate > 5%

**Impacto**: Visibilidad real de performance y costos

**Estimación**: 1 día

---

### 🟢 NICE-TO-HAVE (Mejoras de calidad/features)

#### 0. Export periódico ChromaDB → Google Sheets (Bidireccional sync)
**Problema**: Los temas agregados por Telegram van a ChromaDB pero no se sincronizan de vuelta al Sheet.

**Solución**: Script que exporta todos los temas de ChromaDB al Sheet periódicamente.
```python
# scripts/export_chromadb_to_sheet.py
def export_all_topics_to_sheet(sheet_id: str):
    # 1. Get all topics from ChromaDB
    topics = get_all_topics_from_chromadb()

    # 2. Format for Sheet (ID, Abstract, Source, etc)
    rows = format_for_sheet(topics)

    # 3. Clear existing Sheet data
    clear_sheet(sheet_id, range='Topics!A2:E')

    # 4. Write all topics to Sheet
    write_to_sheet(sheet_id, rows)
```

**Frecuencia sugerida**: Semanal (domingo a las 2 AM)

**Beneficios**:
- Sheet siempre tiene TODOS los temas actualizados
- Backup visual completo en Sheet
- Puedes revisar/editar/categorizar en Sheet
- Temas de Telegram se vuelven editables

**Configuración**:
```bash
# Crear Cloud Scheduler job para export semanal
gcloud scheduler jobs create http export-topics-weekly \
  --schedule "0 2 * * 0" \
  --time-zone "Europe/Madrid" \
  --uri "https://europe-west1-run.googleapis.com/.../export-topics-job:run"
```

**Estimación**: 3 horas

---

## 🟢 NICE-TO-HAVE (Mejoras de calidad/features)

#### 7. Implementar feedback loop
**Problema**: No trackeas qué variantes (short/mid/long) prefieren los usuarios.

**Solución**:
```python
# En _finalize_choice(), loggear:
approved_variant_metrics = {
    "short": count_A / total,
    "mid": count_B / total,
    "long": count_C / total
}

# Ajustar estrategia de generación según preferencias
if approved_variant_metrics["short"] > 0.6:
    # Los usuarios prefieren short, optimizar para eso
```

**Impacto**: Mejora iterativa basada en datos reales

**Estimación**: 4 horas

---

#### 8. Circuit breaker robusto para LLM
**Problema**: Si OpenAI se cae, tu bot se cae. El circuit breaker actual (60s timeout) es naive.

**Solución**:
```python
class LLMCircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=300):
        self.failures = 0
        self.last_failure_time = None

    def call(self, func, *args, **kwargs):
        if self.is_open():
            raise CircuitOpenError("Circuit is open")
        try:
            result = func(*args, **kwargs)
            self.reset()
            return result
        except Exception as e:
            self.record_failure()
            raise
```

**Impacto**: Mayor resiliencia ante fallos de proveedores

**Estimación**: 3 horas

---

#### 9. Generación nativa por length (vs truncation)
**Problema**: MID y SHORT se crean truncando LONG. Pierdes calidad.

**Solución**: Generar cada variante nativa al target length:
```python
# Prompt específico para cada longitud
generate_short(topic, target=140)  # Nativo
generate_mid(topic, target=200)    # Nativo
generate_long(topic, target=270)   # Nativo
```

**Pros**: Mejor calidad por variante
**Contras**: 3x más LLM calls (más caro, más lento)

**Estimación**: 1 día (cambio en simple_generator.py)

---

#### 10. A/B testing framework
**Problema**: No sabes qué generaciones performen mejor en engagement real.

**Solución**:
- Trackear qué tweets se publican
- Medir engagement (si tienes acceso a métricas de Threads/X)
- Correlacionar con características del tweet (length, style score, etc)

**Impacto**: Optimización basada en resultados reales

**Estimación**: 2 días

---

## Roadmap Sugerido

### Sprint 1 (1 semana): Clean up crítico
1. Eliminar código muerto → `/legacy`
2. Unificar sistema de mensajes
3. Downgrade embeddings a small

### Sprint 2 (1 semana): Refactor arquitectónico
4. Refactorizar `proposal_service.py` en servicios modulares
5. Tests unitarios para cada servicio

### Sprint 3 (1 semana): Performance & Observabilidad
6. Tunear ChromaDB HNSW
7. Implementar observabilidad seria (dashboard + alertas)
8. Circuit breaker robusto

### Sprint 4 (1 semana): Data-driven improvements
9. Feedback loop (tracking de preferencias)
10. A/B testing framework (si aplica)

---

## Notas

- Este roadmap asume que el flujo actual funciona correctamente
- Las estimaciones son para 1 desarrollador tiempo completo
- Prioriza según dolor actual: si modularidad es crítica, empieza por refactor
- Si costo es crítico, empieza por embeddings small
- Si no tienes visibilidad, empieza por observabilidad

---

**Última actualización**: 2025-11-06
**Próxima revisión**: Cuando se implemente alguna mejora o aparezcan nuevos problemas
