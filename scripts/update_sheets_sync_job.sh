#!/bin/bash
# Script para actualizar el Cloud Run Job de sync de Google Sheets
# con la imagen más reciente que incluye los scripts

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-xbot-473616}"
REGION="${REGION:-europe-west1}"
JOB_NAME="sync-topics-daily"
SERVICE_NAME="x-bot-mei"
REPO="x-bot-mei"

echo "=================================================="
echo "Updating Cloud Run Job: $JOB_NAME"
echo "=================================================="

# Get the latest image tag from the Cloud Run Service
echo ""
echo "1. Getting latest image from Cloud Run Service..."
LATEST_IMAGE=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" \
  --format="value(spec.template.spec.containers[0].image)" \
  --project="$PROJECT_ID")

echo "   Latest image: $LATEST_IMAGE"

# Update the Cloud Run Job with the new image
echo ""
echo "2. Updating Cloud Run Job with latest image..."
gcloud run jobs update "$JOB_NAME" \
  --image="$LATEST_IMAGE" \
  --region="$REGION" \
  --project="$PROJECT_ID"

echo ""
echo "✅ Job updated successfully!"

# Show job details
echo ""
echo "3. Job configuration:"
gcloud run jobs describe "$JOB_NAME" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --format="table(metadata.name,spec.template.spec.containers[0].image,spec.template.spec.containers[0].command,spec.template.spec.containers[0].args)"

echo ""
echo "=================================================="
echo "You can test the job manually with:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
echo "=================================================="
