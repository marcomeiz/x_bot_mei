Acceso y Operación (CLI) — x_bot_mei

Repositorio
- URL (SSH): git@github.com:marcomeiz/x_bot_mei.git
- Rama principal: main
- Actions: https://github.com/marcomeiz/x_bot_mei/actions

Git + GitHub (SSH)
- Requiere git y openssh instalados.
- Clonar: git clone git@github.com:marcomeiz/x_bot_mei.git
- Remoto actual (verificar): git remote -v → origin git@github.com:marcomeiz/x_bot_mei.git
- Empujar cambios: git add -A && git commit -m "…" && git push origin main

GCP — Proyecto y servicio
- Project ID: xbot-473616
- Project Number: 295511624125
- Región: europe-west1
- Cloud Run service: x-bot-mei
- Service URL: https://x-bot-mei-fi5yfc7dia-ew.a.run.app
- Logs servicio (UI): https://console.cloud.google.com/run/detail/europe-west1/x-bot-mei/logs?project=xbot-473616
- Cloud Build (UI): https://console.cloud.google.com/cloud-build/builds?project=295511624125

CI/CD
- Workflow: .github/workflows/deploy.yml
- Disparo: push a main o Run workflow (workflow_dispatch)
- Auth: Workload Identity Federation (WIF)
  - Workload Identity Pool: github-pool
  - Provider: github-provider
  - Provider path: projects/295511624125/locations/global/workloadIdentityPools/github-pool/providers/github-provider
- Service Account (deploy desde Actions): gha-deploy@xbot-473616.iam.gserviceaccount.com
- Variables de repo (GitHub → Settings → Secrets and variables → Actions → Variables):
  - GCP_PROJECT_ID = xbot-473616
  - GCP_REGION = europe-west1
  - CLOUD_RUN_SERVICE = x-bot-mei
  - ARTIFACT_REPO = x-bot-mei
  - DB_BUCKET = xbot-473616-x-bot-mei-db

Infra y despliegue (gcloud)
- Proyecto activo: gcloud config set project xbot-473616
- Script IAM idempotente: deploy/iam_setup.sh
  - Concede roles a:
    - Deploy SA (Actions): roles/cloudbuild.builds.editor
    - Cloud Build SA: roles/run.admin, roles/artifactregistry.writer, roles/secretmanager.secretAccessor
    - Cloud Run runtime SA: roles/secretmanager.secretAccessor, roles/storage.objectAdmin
- Repositorio de Artifact Registry: x-bot-mei (region europe-west1)
- Bucket GCS (DB Chroma): gs://xbot-473616-x-bot-mei-db

Secrets (GCP Secret Manager)
- TELEGRAM_BOT_TOKEN (requiere para notificaciones y webhook)
- TELEGRAM_CHAT_ID
- GOOGLE_API_KEY (obligatoria para embeddings y Gemini)
- ADMIN_API_TOKEN (obligatoria para /stats y endpoints admin)
- OPENROUTER_API_KEY (opcional; fallback)
- Acceso (si tienes permisos):
  - gcloud secrets versions access latest --secret=TELEGRAM_BOT_TOKEN --project=xbot-473616
  - gcloud secrets versions access latest --secret=TELEGRAM_CHAT_ID --project=xbot-473616
  - gcloud secrets versions access latest --secret=GOOGLE_API_KEY --project=xbot-473616
  - gcloud secrets versions access latest --secret=ADMIN_API_TOKEN --project=xbot-473616
  - gcloud secrets versions access latest --secret=OPENROUTER_API_KEY --project=xbot-473616

Runtime (Cloud Run)
- Variables de entorno (configuradas en el despliegue):
  - CHROMA_DB_PATH = /mnt/db
  - GEMINI_MODEL = gemini-2.5-pro
  - FALLBACK_PROVIDER_ORDER = gemini,openrouter
  - SHOW_TOPIC_ID = 0
- Secretos inyectados (como variables):
  - TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GOOGLE_API_KEY, ADMIN_API_TOKEN, (OPENROUTER_API_KEY si existe)
- Volumen: GCS FUSE /mnt/db ⇒ bucket xbot-473616-x-bot-mei-db

Endpoints de la app
- Webhook Telegram: /<TELEGRAM_BOT_TOKEN>
- Salud: /
- Stats: /stats?token=<ADMIN_API_TOKEN>
- PDFs (admin): /pdfs?token=<ADMIN_API_TOKEN>
- Ingesta (admin POST): /ingest_topics?token=<ADMIN_API_TOKEN>

Logs (CLI)
- Últimos logs servicio: gcloud run services logs read x-bot-mei --region europe-west1 --limit=200
- Grep rápido (errores): gcloud run services logs read x-bot-mei --region europe-west1 --limit=400 | rg "ERROR|WARNING|Telegram"

Notas de operación
- Push a main ⇒ despliega automáticamente (Actions → Cloud Build → Cloud Run).
- Telegram formateado en HTML (parse_mode=HTML) para evitar errores 400 y texto con barras invertidas.
- Generación LLM: Gemini 2.5 Pro para A/B/C; fallback robusto si falla el JSON.
- Similitud: no bloquea en generación; se valida al aprobar con confirmación si es muy parecido.

