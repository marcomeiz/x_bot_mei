#!/usr/bin/env bash
set -euo pipefail

# Grants required IAM roles for:
#  - GitHub Actions deploy SA (WIF): submit Cloud Build
#  - Cloud Build SA: deploy to Cloud Run, push to Artifact Registry, read secrets
#  - Cloud Run runtime SA: read secrets and access GCS bucket via GCS FUSE
# Also ensures core APIs enabled.

PROJECT_ID=${PROJECT_ID:-xbot-473616}
REGION=${REGION:-europe-west1}
DEPLOY_SA_EMAIL=${DEPLOY_SA_EMAIL:-gha-deploy@${PROJECT_ID}.iam.gserviceaccount.com}

echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"
echo "Deploy SA (GHA): ${DEPLOY_SA_EMAIL}"

echo "Setting project..."
gcloud config set project "$PROJECT_ID" >/dev/null

echo "Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com >/dev/null

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
CB_SA_EMAIL="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
RUN_SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "Granting roles to GitHub Actions deploy SA (Cloud Build submit)..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA_EMAIL}" \
  --role="roles/cloudbuild.builds.editor" \
  --condition=None >/dev/null

echo "Granting roles to Cloud Build SA..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CB_SA_EMAIL}" \
  --role="roles/run.admin" \
  --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CB_SA_EMAIL}" \
  --role="roles/artifactregistry.writer" \
  --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CB_SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None >/dev/null

echo "Granting roles to Cloud Run runtime SA..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUN_SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUN_SA_EMAIL}" \
  --role="roles/storage.objectAdmin" \
  --condition=None >/dev/null

echo "Done. Review any IAM warnings above."
