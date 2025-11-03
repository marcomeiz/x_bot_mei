Deploy to Google Cloud Run + GCS (durable storage)

Prereqs
- gcloud CLI authenticated to your Google account.
- Billing enabled on the project; Artifact Registry and Cloud Run APIs enabled.

Quick Start
1) Export minimal variables (adjust names):
   - export PROJECT_ID="your-project-id"
   - export REGION="europe-west1"     # or us-central1
   - export SERVICE="x-bot-mei"
   - export REPO="x-bot-mei"          # Artifact Registry repo name
   - export BUCKET_DB="x-bot-mei-db"
   - export BUCKET_BACKUP="x-bot-mei-backups"

2) Export secrets (or let the script prompt):
   - export TELEGRAM_BOT_TOKEN="..."
   - export TELEGRAM_CHAT_ID="..."     # chat or group id for build/deploy notifications
   - export OPENROUTER_API_KEY="..."   # requerido para LLM y embeddings vía OpenRouter
   - export ADMIN_API_TOKEN="choose-a-strong-token"

3) Run the deploy script from the repo root:
   - bash deploy/deploy_cloud_run.sh

What it does
- Creates GCS buckets ($BUCKET_DB for ChromaDB, $BUCKET_BACKUP for code backups).
- Builds and pushes the Docker image to Artifact Registry.
- Deploys Cloud Run service with GCS FUSE mounted at /mnt/db and CHROMA_DB_PATH=/mnt/db.
- Stores secrets in Secret Manager and injects them at runtime.
- Sets Telegram webhook to https://<service-url>/<TELEGRAM_BOT_TOKEN>.
- Prints /stats endpoint to verify counts.
 - Sends Telegram notifications for deploy start/success (manual script and Cloud Build pipeline).

Verify
- curl "https://<service-url>/stats?token=$ADMIN_API_TOKEN"
- Approve a tweet in Telegram and you should see: "✅ Añadido a la memoria. Ya hay X publicaciones." and the count reflected in stats.

Seed existing DB (optional)
- gsutil -m rsync -r db gs://$BUCKET_DB

Back up code snapshot
- bash deploy/deploy_cloud_run.sh backup   # or run the gsutil rsync inside the script

CI/CD (auto‑deploy on push)

Recommended: GitHub Actions + Workload Identity Federation (WIF) calling Cloud Build. This keeps the full pipeline in-repo and avoids long‑lived keys. Alternatively, a Cloud Build Trigger also works.

1) Prepare once (project‑level):
   - Ensure APIs are enabled (already done by the script): Run, Artifact Registry, Cloud Build, Secret Manager, Cloud Storage.
   - Create secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OPENROUTER_API_KEY, ADMIN_API_TOKEN.
   - Grant roles to the Cloud Build SA (PROJECT_NUMBER@cloudbuild.gserviceaccount.com):
     - roles/run.admin
     - roles/artifactregistry.writer
     - If you deploy with a custom service account for Cloud Run, also grant roles/iam.serviceAccountUser on that SA.
   - Ensure Cloud Run SA (PROJECT_NUMBER-compute@developer.gserviceaccount.com) has:
     - roles/secretmanager.secretAccessor
     - roles/storage.objectAdmin (to mount the GCS bucket with GCS FUSE)

2) Add the provided pipeline file:
   - deploy/cloudbuild.yaml (already in repo) builds the image and deploys Cloud Run with:
     - GCS volume mount (type=cloud-storage) at /mnt/db
     - Env vars, including a comma value via gcloud escaping
     - Secret references from Secret Manager (including TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID for Telegram notifications)
     - Notifications to Telegram at start and success with branch, short SHA and commit subject

GitHub Actions (recommended)
1) Create a deploy Service Account (SA) and grant roles:
   - SA: <deploy‑sa>@$PROJECT_ID.iam.gserviceaccount.com
   - roles: run.admin, artifactregistry.writer, secretmanager.secretAccessor
   - Ensure Cloud Run SA (PROJECT_NUMBER-compute@developer.gserviceaccount.com) has: secretmanager.secretAccessor, storage.objectAdmin
2) Create a Workload Identity Pool + Provider (OIDC for GitHub) and bind the SA:
   - Pool: github-pool, Provider: github-provider (issuer https://token.actions.githubusercontent.com)
   - Attribute mapping must include repository and ref.
   - Bind WIF to the repo: roles/iam.workloadIdentityUser for attribute.repository=<OWNER>/<REPO>
3) Add GitHub repository Secrets/Variables:
   - Secrets: GCP_WORKLOAD_IDENTITY_PROVIDER, GCP_SERVICE_ACCOUNT_EMAIL
   - Variables: GCP_PROJECT_ID, GCP_REGION (europe-west1), CLOUD_RUN_SERVICE (x-bot-mei), ARTIFACT_REPO (x-bot-mei), DB_BUCKET (x-bot-mei-db)
4) The workflow .github/workflows/deploy.yml (added) authenticates via WIF and runs:
   - gcloud builds submit --config deploy/cloudbuild.yaml --substitutions=_REGION,_SERVICE,_REPO,_BUCKET_DB
5) Push to main → GitHub Actions triggers Cloud Build → deploys to Cloud Run with Telegram notifications.

Cloud Build Trigger (alternative)
1) Cloud Build → Triggers → Create trigger
   - Source: Connect GitHub repository (via Google Cloud Build GitHub App)
   - Event: Push to a branch (e.g., main)
   - Configuration: Use a YAML file → Path: deploy/cloudbuild.yaml
   - Substitutions (_REGION, _SERVICE, _REPO, _BUCKET_DB) as desired
2) Commit to main → auto‑build and auto‑deploy.

Notes
- The pipeline doesn’t mutate IAM each run. Run the one‑time IAM grants above or keep the manual deploy script for first‑time setup.
- OPENROUTER_API_KEY is injected only if the secret exists in the project.
- If you prefer GitHub Actions, mirror these steps with Workload Identity Federation and the same gcloud commands.

Current Trigger
- Name: `activadorx`
- Region: `europe-west1`
- Manual run:
  - `gcloud builds triggers run activadorx --project=xbot-473616 --region=europe-west1 --branch=main`

Troubleshooting (quick)
- Substitutions errors like "SECRET_LIST" or "IMG" not valid:
  - Cloud Build substitutes `$VAR` in the YAML. We escape bash vars with `$$` inside `deploy/cloudbuild.yaml`.
  - Ensure the file uses `$$IMG`, `$$ENV_VARS`, `$$SECRET_LIST` and appends with `"$$SECRET_LIST,..."`.
- Secret Manager API disabled:
  - Enable `secretmanager.googleapis.com`. The deploy script already enables it.
- Cloud Run GCS mount errors:
  - Use `--add-volume type=cloud-storage` and grant `roles/storage.objectAdmin` to the Cloud Run SA.
- Forbidden on /stats:
  - Use the `ADMIN_API_TOKEN` value; rotate by uploading a new secret version.
 - Telegram notifications missing:
   - Ensure secrets TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID exist in Secret Manager and that the Cloud Build SA has `roles/secretmanager.secretAccessor`.
   - Verify chat id is correct (use @userinfobot or a quick bot echo). Check Cloud Build logs for the notify steps `notify-start` and `Deploy to Cloud Run` tail.
