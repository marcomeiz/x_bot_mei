# Sistema de Gestión de Temas - Documentación Completa

**Fecha de implementación**: 2025-11-06
**Sistema**: Híbrido Google Sheets + Telegram + ChromaDB
**Proyecto GCP**: xbot-473616

---

## 📋 Tabla de Contenidos

1. [Resumen Ejecutivo](#resumen-ejecutivo)
2. [Arquitectura del Sistema](#arquitectura-del-sistema)
3. [Componentes](#componentes)
4. [Comandos de Usuario](#comandos-de-usuario)
5. [Configuración Inicial](#configuración-inicial)
6. [Flujo de Datos](#flujo-de-datos)
7. [Mantenimiento](#mantenimiento)
8. [Troubleshooting](#troubleshooting)
9. [Mejoras Futuras](#mejoras-futuras)

---

## 1. Resumen Ejecutivo

Sistema híbrido que permite gestionar temas para generación de tweets sin tocar código:

- **Telegram** → Agregar temas instantáneamente desde móvil/desktop
- **Google Sheets** → Gestionar temas en bulk, revisar, editar, categorizar
- **ChromaDB** → Base de datos vectorial central con embeddings
- **Sync diario** → Google Sheets → ChromaDB a las 3 AM automáticamente

### Beneficios
- ✅ Sin tocar código para agregar temas
- ✅ Detección automática de duplicados (cosine similarity)
- ✅ Agregar desde móvil con `/tema` instantáneo
- ✅ Gestión bulk en spreadsheet familiar
- ✅ Backup visible en Google Sheets
- ✅ Audit trail completo (source tracking)

---

## 2. Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────┐
│                     ChromaDB (Central)                       │
│                 Almacena: topics + embeddings                │
│                     Total actual: 53 temas                   │
└─────────────────────────────────────────────────────────────┘
           ▲                                    ▲
           │                                    │
    [Sync diario 3 AM]                   [Instantáneo]
           │                                    │
           │                                    │
┌──────────────────────┐          ┌────────────────────────┐
│   Google Sheets      │          │      Telegram Bot      │
│   (Gestión bulk)     │          │     /tema <text>       │
│                      │          │     /temas             │
│   Sheet ID:          │          │                        │
│   10cflUVvgh...23U   │          │   Chat ID: tu_chat     │
└──────────────────────┘          └────────────────────────┘
           │                                    │
           ├────────────────────────────────────┤
                      Acceso vía:
           │                                    │
┌──────────▼──────────┐          ┌─────────────▼──────────┐
│  Service Account    │          │   Cloud Run Service    │
│  topics-sync-service│          │      x-bot-mei         │
│  @xbot-473616...    │          │   (Bot principal)      │
└─────────────────────┘          └────────────────────────┘
```

---

## 3. Componentes

### 3.1 Google Sheets

**URL**: https://docs.google.com/spreadsheets/d/10cflUVvgh6UBMmlSvB2qOlNb5QFYBpqEsg4KQcIF23U
**Nombre**: X Bot Mei - Topics
**Pestaña**: Topics

**Estructura de columnas**:

| Columna | Nombre | Tipo | Descripción |
|---------|--------|------|-------------|
| A | ID | String | Identificador único del tema (opcional, se autogenera) |
| B | Abstract | String | Texto del tema (REQUERIDO, min 20 chars) |
| C | Source PDF | String | Origen del tema si viene de PDF (opcional) |
| D | Approved | Boolean | TRUE/FALSE, marca temas pre-aprobados |
| E | Notes | String | Notas adicionales (opcional) |

**Permisos**:
- Compartido con: `topics-sync-service@xbot-473616.iam.gserviceaccount.com` (Viewer)
- El Service Account solo tiene **lectura**

---

### 3.2 Service Account

**Email**: `topics-sync-service@xbot-473616.iam.gserviceaccount.com`
**Propósito**: Acceder a Google Sheets API para leer temas
**Key JSON**: Almacenado en Secret Manager como `topics-sync-credentials`

**Permisos**:
- Google Sheets API: Lectura del Sheet específico
- Secret Manager: Acceso desde Cloud Run Service y Job

---

### 3.3 Cloud Run Service (Bot Principal)

**Nombre**: `x-bot-mei`
**Región**: `europe-west1`
**URL**: https://x-bot-mei-295511624125.europe-west1.run.app

**Variables de entorno relevantes**:
```bash
TOPICS_SHEET_ID=10cflUVvgh6UBMmlSvB2qOlNb5QFYBpqEsg4KQcIF23U
GOOGLE_SHEETS_CREDENTIALS_PATH=/secrets/topics-sync-credentials/key.json
CHROMA_DB_URL=https://x-chroma-295511624125.europe-west1.run.app
SIM_DIM=3072
TOPICS_COLLECTION=topics_collection_3072
```

**Secrets montados**:
- `/secrets/topics-sync-credentials/key.json` → Service Account JSON key

---

### 3.4 Cloud Run Job (Sync Diario)

**Nombre**: `sync-topics-daily`
**Región**: `europe-west1`
**Script**: `scripts/sync_sheets_to_chromadb.py`

**Funcionalidad**:
1. Lee todos los temas del Google Sheet
2. Obtiene todos los IDs existentes en ChromaDB
3. Detecta temas nuevos (Sheet - ChromaDB)
4. Genera embeddings para temas nuevos
5. Ingesta temas nuevos a ChromaDB con metadata
6. Loggea resultados

**Configuración**:
```bash
--command python3
--args scripts/sync_sheets_to_chromadb.py
--task-timeout 10m
--max-retries 2
```

---

### 3.5 Cloud Scheduler

**Nombre**: `sync-topics-3am`
**Región**: `europe-west1`
**Schedule**: `0 3 * * *` (Diario a las 3 AM, Europe/Madrid timezone)
**Target**: Cloud Run Job `sync-topics-daily`

**Autenticación**: OAuth con Service Account `295511624125-compute@developer.gserviceaccount.com`

---

### 3.6 Módulos Python

#### `topic_manager.py`
**Funciones principales**:
- `add_topic(abstract, source='telegram', approved=False)` → Agrega tema a ChromaDB
- `get_topics_count()` → Retorna total de temas
- `list_recent_topics(limit=10)` → Lista últimos N temas
- `generate_topic_id(abstract)` → Genera ID único basado en texto + timestamp

**Validaciones**:
- Abstract no vacío
- Min 20 caracteres, max 500
- Detección de duplicados (cosine similarity < 0.1)

#### `scripts/sync_sheets_to_chromadb.py`
**Funciones principales**:
- `read_topics_from_sheet(sheet_id)` → Lee temas del Sheet
- `get_existing_topic_ids()` → Obtiene IDs de ChromaDB
- `ingest_topic(topic)` → Ingesta 1 tema con embedding
- `sync_sheets_to_chromadb(sheet_id, dry_run=False)` → Main sync logic

**Flags**:
```bash
--dry-run  # Ver qué haría sin ejecutar cambios
```

---

## 4. Comandos de Usuario

### 4.1 Telegram

#### `/tema <texto>`
Agrega un tema instantáneamente.

**Ejemplo**:
```
/tema Pricing psychology: anchor on value delivered, not hours worked
```

**Respuesta**:
```
🔄 Agregando tema...

✅ Tema agregado con ID: pricing-psychology-anchor-20251106193531

📊 Total de temas: 54
```

**Validaciones**:
- Texto no vacío
- Min 20 caracteres, max 500
- No duplicado (similarity < 0.1)

**Errores comunes**:
```
❌ El tema debe tener al menos 20 caracteres
❌ Tema muy similar ya existe: seed:pricing-ladder
❌ Error generando embedding
```

---

#### `/temas`
Lista los últimos 10 temas agregados.

**Respuesta**:
```
📚 Últimos 10 temas (total: 54)

• pricing-psychology-anchor-20251106193531
  Pricing psychology: anchor on value delivered, not hours worked
  Fuente: telegram

• seed:owner-boundaries
  Owner boundaries: protect energy, say no, enforce scope
  Fuente: rebuild

...
```

---

### 4.2 Google Sheets

#### Agregar temas en bulk

1. Abre el Sheet: https://docs.google.com/spreadsheets/d/10cflUVvgh6UBMmlSvB2qOlNb5QFYBpqEsg4KQcIF23U
2. Agrega filas nuevas al final:

| ID | Abstract | Source PDF | Approved | Notes |
|---|---|---|---|---|
| cash-runway-discipline | Cash runway: 6 months minimum or you're flying blind | | FALSE | Key metric |
| client-selection-criteria | Client selection: say no to scope creep early, qualify hard | | FALSE | ICP clarity |

3. El ID es opcional (se autogenera si vacío)
4. **Espera hasta las 3 AM** para sync automático
5. O ejecuta sync manual (ver comandos abajo)

---

### 4.3 Comandos CLI (Mantenimiento)

#### Ejecutar sync manualmente
```bash
gcloud run jobs execute sync-topics-daily \
  --region europe-west1 \
  --project xbot-473616 \
  --wait
```

#### Dry run (ver qué haría sin ejecutar)
```bash
# Crear versión temporal del job con --dry-run
gcloud run jobs execute sync-topics-daily \
  --region europe-west1 \
  --project xbot-473616 \
  --args scripts/sync_sheets_to_chromadb.py,--dry-run \
  --wait
```

#### Ver logs del sync
```bash
# Últimos 50 logs del job
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=sync-topics-daily" \
  --limit 50 \
  --project xbot-473616 \
  --format json

# Filtrar solo errores
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=sync-topics-daily AND severity>=ERROR" \
  --limit 20 \
  --project xbot-473616
```

#### Ver configuración del scheduler
```bash
gcloud scheduler jobs describe sync-topics-3am \
  --location europe-west1 \
  --project xbot-473616
```

#### Pausar/reactivar sync diario
```bash
# Pausar
gcloud scheduler jobs pause sync-topics-3am \
  --location europe-west1 \
  --project xbot-473616

# Reactivar
gcloud scheduler jobs resume sync-topics-3am \
  --location europe-west1 \
  --project xbot-473616
```

---

## 5. Configuración Inicial

Documentación completa en: `TOPIC_MANAGEMENT_GUIDE.md`

**Resumen de pasos completados**:

1. ✅ Habilitar Google Sheets API en GCP
2. ✅ Crear Service Account (`topics-sync-service`)
3. ✅ Descargar JSON key y subirlo a Secret Manager (`topics-sync-credentials`)
4. ✅ Dar permisos al Service Account para acceder al secret
5. ✅ Crear Google Sheet y compartirlo con Service Account (viewer)
6. ✅ Configurar variables de entorno en Cloud Run
7. ✅ Montar secret como archivo en Cloud Run Service
8. ✅ Crear Cloud Run Job para sync
9. ✅ Crear Cloud Scheduler para ejecución diaria (3 AM)
10. ✅ Deploy y pruebas exitosas

**Fecha de setup**: 2025-11-06

---

## 6. Flujo de Datos

### 6.1 Agregar tema desde Telegram

```
Usuario en Telegram
    |
    | /tema <texto>
    ▼
bot.py::_handle_add_topic()
    |
    | Validar texto (20-500 chars)
    ▼
topic_manager.py::add_topic()
    |
    | 1. Generar ID único
    | 2. Generar embedding (OpenAI/OpenRouter)
    | 3. Query ChromaDB para duplicados (similarity < 0.1)
    |
    ▼
    [Duplicado?]
    |    |
    No   Si → ❌ "Tema muy similar ya existe"
    |
    ▼
ChromaDB.add()
    |
    | embeddings=[embedding]
    | documents=[abstract]
    | metadatas=[{source: 'telegram', created_at, ...}]
    ▼
✅ Confirmación a usuario
```

**Tiempo**: ~2-5 segundos (generación de embedding + query)

---

### 6.2 Sync diario desde Google Sheets

```
Cloud Scheduler (3 AM)
    |
    | HTTP POST
    ▼
Cloud Run Job: sync-topics-daily
    |
    | python3 scripts/sync_sheets_to_chromadb.py
    ▼
1. Autenticación con Service Account
    |
    ▼
2. Google Sheets API: Read all topics
    |
    | GET spreadsheets/ID/values/Topics!A2:E
    ▼
3. ChromaDB: Get existing IDs
    |
    | topics_collection.get(include=[])
    ▼
4. Detectar temas nuevos
    |
    | new_topics = sheet_topics - chromadb_ids
    ▼
5. Para cada tema nuevo:
    |
    | a. Generar embedding
    | b. Agregar a ChromaDB con metadata:
    |    {source: 'google_sheets', created_at, ...}
    ▼
6. Log resultados
    |
    | Read: X, New: Y, Ingested: Z, Failed: W
    ▼
✅ Job completo
```

**Duración**: ~1-5 minutos (depende de # de temas nuevos)

---

### 6.3 Flujo completo de un tema

```
Día 1, 10 AM:
    Usuario agrega en Sheet: "New topic about X"
    → Sheet tiene el tema
    → ChromaDB NO tiene el tema (hasta las 3 AM)

Día 2, 3 AM:
    Scheduler triggerea sync-topics-daily
    → Lee Sheet (encuentra "New topic about X")
    → Compara con ChromaDB (no existe)
    → Genera embedding
    → Ingesta a ChromaDB
    ✅ Ahora está en ChromaDB

Día 2, 10 AM:
    Usuario ejecuta /g
    → Bot selecciona tema desde ChromaDB
    → "New topic about X" puede ser seleccionado
```

---

## 7. Mantenimiento

### 7.1 Monitoreo

**Métricas clave**:
- Total de temas en ChromaDB: `get_topics_count()`
- Tasa de éxito del sync diario: Ver logs
- Errores de embedding: Ver logs de Cloud Run Service

**Comandos útiles**:
```bash
# Ver total de temas actual
python3 -c "from topic_manager import get_topics_count; print(f'Total: {get_topics_count()}')"

# Ver logs del bot (últimas 24h)
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=x-bot-mei" \
  --limit 100 \
  --freshness=1d \
  --project xbot-473616
```

---

### 7.2 Backup

**ChromaDB**:
- Almacenado en Cloud Storage bucket: `xbot-473616-x-bot-mei-db`
- Backup automático por persistencia de GCS

**Google Sheet**:
- Historial de versiones nativo de Google Sheets
- File → Version history

**Recomendación**: Export periódico de ChromaDB → Sheet (ver Mejoras Futuras)

---

### 7.3 Rotación de Secrets

Si necesitas rotar el Service Account key:

```bash
# 1. Crear nueva key
gcloud iam service-accounts keys create new-key.json \
  --iam-account=topics-sync-service@xbot-473616.iam.gserviceaccount.com

# 2. Actualizar secret
gcloud secrets versions add topics-sync-credentials \
  --data-file=new-key.json \
  --project=xbot-473616

# 3. Eliminar key antigua
gcloud iam service-accounts keys delete OLD_KEY_ID \
  --iam-account=topics-sync-service@xbot-473616.iam.gserviceaccount.com

# 4. Redeploy Cloud Run (automáticamente usa latest)
```

---

## 8. Troubleshooting

### 8.1 "/tema no responde"

**Síntomas**: El bot no responde al comando `/tema`

**Diagnóstico**:
```bash
# Ver logs del bot
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=x-bot-mei AND textPayload:'/tema'" \
  --limit 10 \
  --project xbot-473616
```

**Causas comunes**:
1. Bot caído → Verificar Cloud Run Service status
2. ChromaDB no disponible → Verificar `CHROMA_DB_URL`
3. Error generando embedding → Verificar `OPENROUTER_API_KEY`

---

### 8.2 "Sync diario no ingesta temas nuevos"

**Síntomas**: Agregaste temas al Sheet pero no aparecen en ChromaDB después de las 3 AM

**Diagnóstico**:
```bash
# Ver logs del último sync
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=sync-topics-daily" \
  --limit 50 \
  --project xbot-473616 \
  --format json | jq -r '.[].textPayload'
```

**Causas comunes**:

| Causa | Solución |
|-------|----------|
| **ID duplicado** | El ID ya existe en ChromaDB → Cambia el ID en el Sheet |
| **Abstract vacío** | La columna B está vacía → Completa el Abstract |
| **Permisos del Sheet** | Service Account sin acceso → Re-compartir Sheet |
| **Secret no accesible** | Error 403 en logs → Verificar IAM del secret |
| **Scheduler pausado** | Job no se ejecuta → `gcloud scheduler jobs resume` |

---

### 8.3 "Error: Google Sheets credentials not found"

**Diagnóstico**:
```bash
# Verificar que el secret existe
gcloud secrets describe topics-sync-credentials --project=xbot-473616

# Verificar que el secret está montado en Cloud Run
gcloud run services describe x-bot-mei \
  --region europe-west1 \
  --project xbot-473616 \
  --format="value(spec.template.spec.containers[0].volumeMounts)"
```

**Solución**:
```bash
# Verificar env var
gcloud run services describe x-bot-mei \
  --region europe-west1 \
  --project xbot-473616 \
  --format="value(spec.template.spec.containers[0].env)" | grep GOOGLE_SHEETS

# Si falta, redeploy con las env vars correctas
```

---

### 8.4 "Tema duplicado detectado incorrectamente"

**Síntomas**: Intentas agregar tema legítimamente diferente pero el bot dice "muy similar"

**Causa**: Threshold de similarity muy estricto (< 0.1)

**Solución temporal**:
- Reformula el abstract para hacerlo más distintivo
- O agrega el tema directamente en el Sheet (el sync NO valida duplicados)

**Solución permanente** (requiere código):
```python
# En topic_manager.py, ajustar threshold
DUPLICATE_THRESHOLD = 0.05  # Más estricto (solo duplicados exactos)
```

---

## 9. Mejoras Futuras

### 9.1 Export periódico ChromaDB → Google Sheet ⭐

**Problema**: Temas agregados por Telegram no aparecen en el Sheet.

**Solución**: Script que exporta TODO de ChromaDB al Sheet semanalmente.

**Beneficios**:
- Sheet siempre sincronizado con ChromaDB
- Backup visual completo
- Puedes editar temas de Telegram en el Sheet

**Implementación**: Ver `OPTIMIZATION_ROADMAP.md` → Item #0

**Estimación**: 3 horas

---

### 9.2 Comando `/tema_edit`

Permitir editar el abstract de un tema existente desde Telegram.

```
/tema_edit pricing-psychology-anchor-20251106193531
Nuevo abstract: Pricing psychology: value-based pricing vs hourly rates
```

**Estimación**: 2 horas

---

### 9.3 Comando `/tema_search`

Buscar temas por keyword.

```
/tema_search pricing

Resultados (3 encontrados):
• pricing-psychology-anchor-20251106193531
  Pricing psychology: anchor on value...
• seed:pricing-ladder
  Build a pricing ladder: starter, core...
...
```

**Estimación**: 2 horas

---

### 9.4 Aprobar/rechazar temas desde Telegram

Marcar temas como aprobados sin ir al Sheet.

```
/tema_approve pricing-psychology-anchor-20251106193531
✅ Tema aprobado
```

**Estimación**: 1 hora

---

### 9.5 Categorización automática

Usar LLM para categorizar temas automáticamente.

```python
# Al ingestar, detectar categoría
category = llm.categorize(abstract)
# Categorías: Strategy, Operations, Sales, Marketing, Finance
```

**Estimación**: 3 horas

---

## 📊 Resumen de Componentes

| Componente | Ubicación | Propósito |
|------------|-----------|-----------|
| **Google Sheet** | https://docs.google.com/.../10cflUVvgh6... | Gestión bulk de temas |
| **Service Account** | topics-sync-service@xbot-473616... | Acceso a Google Sheets API |
| **Secret Manager** | topics-sync-credentials | JSON key del Service Account |
| **Cloud Run Service** | x-bot-mei (europe-west1) | Bot principal con `/tema` y `/temas` |
| **Cloud Run Job** | sync-topics-daily (europe-west1) | Sync diario Sheet → ChromaDB |
| **Cloud Scheduler** | sync-topics-3am (3 AM diario) | Triggerea el sync job |
| **topic_manager.py** | Código | Lógica de add/list topics |
| **sync_sheets_to_chromadb.py** | scripts/ | Script de sync |
| **ChromaDB** | x-chroma service | Base de datos vectorial central |

---

## 📞 Contacto y Soporte

**Documentación relacionada**:
- Setup completo: `TOPIC_MANAGEMENT_GUIDE.md`
- Roadmap de optimizaciones: `OPTIMIZATION_ROADMAP.md`
- Changelog: `CHANGELOG.md`

**Logs y monitoreo**:
- Cloud Build: https://console.cloud.google.com/cloud-build/builds?project=xbot-473616
- Cloud Run: https://console.cloud.google.com/run?project=xbot-473616
- Cloud Scheduler: https://console.cloud.google.com/cloudscheduler?project=xbot-473616
- Logging: https://console.cloud.google.com/logs?project=xbot-473616

**Última actualización**: 2025-11-06
**Versión del sistema**: 1.0.0
