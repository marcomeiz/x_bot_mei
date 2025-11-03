#!/usr/bin/env bash
set -euo pipefail

# Usage: bash deploy/deploy_cloud_run.sh [backup]
# If 'backup' is passed, it will only back up the repository to GCS.

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing dependency: $1" >&2
    exit 1
  fi
}

require gcloud
require gsutil

MODE=${1:-deploy}

# Config (override via env)
PROJECT_ID=${PROJECT_ID:-}
REGION=${REGION:-europe-west1}
SERVICE=${SERVICE:-x-bot-mei}
REPO=${REPO:-x-bot-mei}
BUCKET_DB=${BUCKET_DB:-x-bot-mei-db}
BUCKET_BACKUP=${BUCKET_BACKUP:-x-bot-mei-backups}

TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
ADMIN_API_TOKEN=${ADMIN_API_TOKEN:-}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}

read_secret() {
  local var_name="$1"; shift
  local val="${!var_name:-}"
  if [[ -z "$val" ]]; then
    read -rsp "Enter $var_name: " val
    echo
    export "$var_name"="$val"
  fi
}

if [[ "$MODE" == "backup" ]]; then
  : "${PROJECT_ID:?PROJECT_ID is required}"
  : "${BUCKET_BACKUP:?BUCKET_BACKUP is required}"
  TS=$(date +%Y%m%d-%H%M%S)
  TAR="x_bot_mei_${TS}.tar.gz"
  echo "Creating tarball $TAR ..."
  tar --exclude='venv' --exclude='__pycache__' --exclude='.git' -czf "$TAR" .
  echo "Uploading to gs://$BUCKET_BACKUP/$TAR ..."
  gsutil mb -l ${REGION} gs://$BUCKET_BACKUP || true
  gsutil cp "$TAR" gs://$BUCKET_BACKUP/
  echo "Backup done: gs://$BUCKET_BACKUP/$TAR"
  exit 0
fi

: "${PROJECT_ID:?PROJECT_ID is required}"

echo "Setting project..."
gcloud config set project "$PROJECT_ID" >/dev/null

echo "Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com >/dev/null

echo "Creating buckets if needed..."
gsutil mb -l ${REGION} gs://$BUCKET_DB || true
gsutil mb -l ${REGION} gs://$BUCKET_BACKUP || true

echo "Creating Artifact Registry repo if needed..."
gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" --description="x_bot_mei images" || true

echo "Preparing secrets..."
read_secret TELEGRAM_BOT_TOKEN
read_secret ADMIN_API_TOKEN
read_secret TELEGRAM_CHAT_ID

ensure_secret() {
  local name="$1"; shift
  local value="$1"; shift
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf "%s" "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
  else
    printf "%s" "$value" | gcloud secrets create "$name" --data-file=- >/dev/null
  fi
}

ensure_secret TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  ensure_secret OPENROUTER_API_KEY "$OPENROUTER_API_KEY"
fi
ensure_secret ADMIN_API_TOKEN "$ADMIN_API_TOKEN"
ensure_secret TELEGRAM_CHAT_ID "$TELEGRAM_CHAT_ID"

notify() {
  local msg="$1"; shift || true
  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
    curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d chat_id="${TELEGRAM_CHAT_ID}" \
      --data-urlencode text="$msg" >/dev/null || true
  else
    echo "(notify) ${msg}"
  fi
}

echo "Granting secret access to Cloud Run SA..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA" \
  --role='roles/secretmanager.secretAccessor' >/dev/null

echo "Building image via Cloud Build..."
TAG=$(date +%Y%m%d-%H%M)
IMG="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:$TAG"
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)
SHORT=$(git rev-parse --short HEAD 2>/dev/null || echo "$TAG")
SUBJECT=$(git log -1 --pretty=%s 2>/dev/null || echo "")
notify "🚀 Deploying ${SERVICE} → ${REGION}\nBranch: ${BRANCH}\nCommit: ${SHORT}${SUBJECT:+\nSubject: ${SUBJECT}}"
gcloud builds submit --tag "$IMG" .

echo "Deploying to Cloud Run..."
## Ensure Cloud Run service account can access GCS bucket for GCS FUSE
echo "Granting Storage access to Cloud Run SA for GCS FUSE..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA" \
  --role='roles/storage.objectAdmin' >/dev/null

# Build secret flags dynamically (OPENROUTER only if present)
SECRET_FLAGS=(
  "TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest"
  "ADMIN_API_TOKEN=ADMIN_API_TOKEN:latest"
)
if gcloud secrets describe OPENROUTER_API_KEY >/dev/null 2>&1; then
  SECRET_FLAGS+=("OPENROUTER_API_KEY=OPENROUTER_API_KEY:latest")
fi
# Join as comma-separated list for gcloud flag
SECRET_LIST=$(IFS=, ; echo "${SECRET_FLAGS[*]}")

# Build env var string with custom item delimiter to allow comma in values
# Using gcloud's escaping feature: ^:<delim>^
ENV_VARS='^~^FALLBACK_PROVIDER_ORDER=openrouter~CHROMA_DB_PATH=/mnt/db~GENERATION_MODEL=x-ai/grok-4~VALIDATION_MODEL=x-ai/grok-4-fast~EMBED_MODEL=openai/text-embedding-3-small~SHOW_TOPIC_ID=0'

gcloud run deploy "$SERVICE" \
  --image "$IMG" \
  --region "$REGION" --allow-unauthenticated --port 8080 \
  --max-instances 1 --min-instances 0 \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "$SECRET_LIST" \
  --add-volume=name=dbvol,type=cloud-storage,bucket="$BUCKET_DB" \
  --add-volume-mount=volume=dbvol,mount-path=/mnt/db

RUN_URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
echo "Service: $RUN_URL"

echo "Setting Telegram webhook..."
curl -fsS -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" -d "url=$RUN_URL/$TELEGRAM_BOT_TOKEN" >/dev/null
echo "Webhook set to $RUN_URL/$TELEGRAM_BOT_TOKEN"

echo "Stats:"
curl -fsS "$RUN_URL/stats?token=$ADMIN_API_TOKEN" || true
echo

notify "✅ Deployed ${SERVICE} (${SHORT})\nURL: ${RUN_URL}${SUBJECT:+\n${SUBJECT}}"

echo "Done. To back up code next time: bash deploy/deploy_cloud_run.sh backup"
