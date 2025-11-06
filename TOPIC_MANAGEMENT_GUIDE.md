# Guía de Gestión de Temas

Sistema híbrido para gestionar temas del bot: **Google Sheets** (revisión/bulk) + **Telegram** (agregar rápido on-the-go).

---

## ✅ Qué puedes hacer

### 1. Ver todos los temas en Google Sheets
- Abre el Sheet (link abajo cuando lo configures)
- Revisa, edita, agrega temas en bulk
- Sync automático **1 vez al día a las 3 AM**

### 2. Agregar tema rápido desde Telegram

```
/tema Capital allocation es la skill #1 del solopreneur
```

**Respuesta del bot:**
```
✅ Tema agregado con ID: capital-allocation-es-20251106143022

📊 Total de temas: 51
```

### 3. Ver últimos temas agregados

```
/temas
```

**Respuesta del bot:**
```
📚 Últimos 10 temas (total: 51)

• capital-allocation-es-20251106143022
  Capital allocation es la skill #1 del solopreneur
  Fuente: telegram

• ...
```

---

## 🔧 Configuración Inicial (Solo 1 vez)

### Paso 1: Crear Google Sheet

1. **Crea un nuevo Google Sheet** con este nombre: `X Bot Mei - Topics`

2. **Crea una pestaña llamada `Topics`** con estas columnas:

   | A | B | C | D | E |
   |---|---|---|---|---|
   | ID | Abstract | Source PDF | Approved | Notes |

3. **Importa los temas actuales**:
   - Descarga el CSV: `data/topics_template.csv`
   - File → Import → Upload → Replace spreadsheet

4. **Copia el SHEET ID** de la URL:
   ```
   https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit
                                      ^^^^^^^^^^^^^^^^^^^^^^^^
                                      Este es el SHEET ID
   ```

---

### Paso 2: Configurar Google Sheets API

#### 2.1 Habilitar API en GCP

1. Ve a [Google Cloud Console](https://console.cloud.google.com)
2. Selecciona tu proyecto (el mismo donde está el bot)
3. Ve a **APIs & Services → Library**
4. Busca "Google Sheets API"
5. Click **Enable**

#### 2.2 Crear Service Account

1. Ve a **IAM & Admin → Service Accounts**
2. Click **Create Service Account**
3. Nombre: `topics-sync-service`
4. Rol: Ninguno (no necesita permisos de GCP)
5. Click **Done**

#### 2.3 Crear Key JSON

1. Click en el Service Account que creaste
2. Tab **Keys** → **Add Key → Create new key**
3. Tipo: **JSON**
4. Click **Create** (se descarga automáticamente)
5. **Guarda el archivo** en un lugar seguro

#### 2.4 Compartir Sheet con Service Account

1. Abre el Service Account en GCP Console
2. Copia el **email** (tiene formato: `topics-sync-service@tu-proyecto.iam.gserviceaccount.com`)
3. Abre tu Google Sheet
4. Click **Share** (arriba a la derecha)
5. Pega el email del Service Account
6. Rol: **Viewer** (solo lectura)
7. **Uncheck** "Notify people"
8. Click **Share**

---

### Paso 3: Configurar Variables de Entorno

#### 3.1 Subir el JSON key a Secret Manager

```bash
# Desde tu terminal local (asumiendo que descargaste el key como service-account-key.json)
gcloud secrets create topics-sync-credentials --data-file=service-account-key.json

# Dar permiso a Cloud Run para acceder
gcloud secrets add-iam-policy-binding topics-sync-credentials \
  --member="serviceAccount:YOUR-PROJECT-NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

#### 3.2 Agregar variables de entorno a Cloud Run

En tu archivo `deploy/cloudbuild.yaml` o `.env`:

```bash
TOPICS_SHEET_ID=1AbCdEfGhIjKlMnOpQrStUvWxYz  # El ID que copiaste del Sheet
GOOGLE_SHEETS_CREDENTIALS_PATH=/secrets/topics-sync-credentials/key.json
```

Si usas Cloud Run, monta el secret:

```yaml
# En cloudbuild.yaml o Cloud Run config
secrets:
  - name: topics-sync-credentials
    mountPath: /secrets/topics-sync-credentials
```

---

### Paso 4: Instalar Dependencias

Agregar a `requirements.runtime.txt`:

```
google-auth==2.23.0
google-auth-oauthlib==1.1.0
google-auth-httplib2==0.1.1
google-api-python-client==2.100.0
```

---

### Paso 5: Configurar Cloud Scheduler (Sync diario a las 3 AM)

#### 5.1 Crear Cloud Function para el sync (Opción A - Recomendada)

```bash
# Crear función que ejecuta el script
gcloud functions deploy sync-topics-daily \
  --runtime python311 \
  --trigger-http \
  --entry-point sync_handler \
  --set-env-vars TOPICS_SHEET_ID=TU_SHEET_ID \
  --set-secrets GOOGLE_SHEETS_CREDENTIALS_PATH=topics-sync-credentials:latest \
  --region us-central1 \
  --timeout 300s \
  --no-allow-unauthenticated

# Crear Cloud Scheduler job
gcloud scheduler jobs create http sync-topics-3am \
  --schedule="0 3 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-YOUR-PROJECT.cloudfunctions.net/sync-topics-daily" \
  --http-method=POST \
  --oidc-service-account-email=YOUR-PROJECT@appspot.gserviceaccount.com
```

#### 5.2 O usar Cloud Run Jobs (Opción B)

```bash
# Crear Cloud Run Job
gcloud run jobs create sync-topics-job \
  --image gcr.io/YOUR-PROJECT/x-bot-mei:latest \
  --command python3 \
  --args scripts/sync_sheets_to_chromadb.py \
  --set-env-vars TOPICS_SHEET_ID=TU_SHEET_ID \
  --set-secrets GOOGLE_SHEETS_CREDENTIALS_PATH=topics-sync-credentials:latest \
  --region us-central1 \
  --task-timeout 5m

# Crear Cloud Scheduler job para ejecutar el Run Job
gcloud scheduler jobs create http sync-topics-3am \
  --schedule="0 3 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR-PROJECT/jobs/sync-topics-job:run" \
  --http-method=POST \
  --oauth-service-account-email=YOUR-PROJECT@appspot.gserviceaccount.com
```

---

## 📖 Uso Diario

### Opción 1: Agregar desde Telegram (Instantáneo)

Cuando se te ocurre un tema en el momento:

```
/tema Pricing psychology: anchor on value, not hours
```

El bot:
1. Genera embedding del tema
2. Verifica que no sea duplicado (similarity < 0.1)
3. Lo agrega a ChromaDB inmediatamente
4. Te confirma con el ID

### Opción 2: Agregar desde Google Sheets (Bulk, sync a las 3 AM)

Cuando quieres agregar/revisar varios:

1. Abre el Google Sheet
2. Agrega filas nuevas con este formato:

   | ID | Abstract | Source PDF | Approved | Notes |
   |---|---|---|---|---|
   | pricing-psychology | Pricing psychology: anchor on value, not hours | | FALSE | From book |

3. El ID puede ser cualquier string único (o déjalo vacío, el script lo generará)
4. **Espera hasta las 3 AM** (o ejecuta manualmente el sync, ver abajo)
5. Los temas nuevos se ingestarán automáticamente

---

## 🔨 Comandos Útiles

### Ejecutar sync manualmente (sin esperar a las 3 AM)

```bash
# Opción A: Si configuraste Cloud Function
gcloud functions call sync-topics-daily --region us-central1

# Opción B: Si configuraste Cloud Run Job
gcloud run jobs execute sync-topics-job --region us-central1

# Opción C: Ejecutar localmente (para testing)
python3 scripts/sync_sheets_to_chromadb.py

# Opción D: Dry run (ver qué haría sin ejecutar)
python3 scripts/sync_sheets_to_chromadb.py --dry-run
```

### Ver logs del sync

```bash
# Si usas Cloud Function
gcloud functions logs read sync-topics-daily --limit 50

# Si usas Cloud Run Job
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=sync-topics-job" --limit 50 --format json
```

---

## 🚨 Troubleshooting

### Error: "Google Sheets credentials not found"

**Solución:**
1. Verifica que la variable `GOOGLE_SHEETS_CREDENTIALS_PATH` esté correctamente configurada
2. Verifica que el secret esté montado en Cloud Run:
   ```bash
   gcloud run services describe YOUR-SERVICE --region us-central1 | grep -A 5 secrets
   ```

### Error: "The caller does not have permission"

**Solución:**
1. Verifica que compartiste el Sheet con el Service Account email
2. Asegúrate de darle al menos rol "Viewer"

### Error: "Topic muy similar ya existe"

**Comportamiento esperado.** El sistema detecta duplicados automáticamente.

Si realmente quieres agregar un tema similar:
1. Hazlo más específico/diferente
2. O revisa si el tema existente es suficiente

### Sync no detecta temas nuevos

**Posibles causas:**
1. El ID del tema ya existe en ChromaDB → Usa un ID diferente
2. El Abstract está vacío → Completa la columna B
3. Faltan las columnas A/B → Verifica el formato del Sheet

---

## 📊 Estadísticas

Para ver cuántos temas tienes:

```
/temas
```

O consulta Cloud Logging:

```bash
gcloud logging read "textPayload:\"Total topics:\"" --limit 1 --format json
```

---

## 🔐 Seguridad

- ✅ El Service Account **solo tiene acceso de lectura** al Sheet
- ✅ El JSON key está en **Secret Manager**, no en código
- ✅ Cloud Scheduler usa **OIDC authentication**, no es público
- ✅ Detección de duplicados previene spam

---

## 🎯 Resumen

| Método | Cuándo usar | Delay | Verificación duplicados |
|--------|-------------|-------|------------------------|
| **Telegram `/tema`** | Tema urgente, on-the-go | Instantáneo | ✅ Sí (cosine < 0.1) |
| **Google Sheets** | Bulk, revisión, edición | Hasta 3 AM | ✅ Sí (por ID + cosine) |

---

**¿Dudas?** Revisa los logs o ejecuta el sync en modo dry-run para ver qué sucedería sin hacer cambios.
