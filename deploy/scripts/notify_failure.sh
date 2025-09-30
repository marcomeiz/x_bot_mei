#!/usr/bin/env bash
set -euo pipefail

# Usage: notify_failure.sh <service> <region> <stage>
SERVICE="${1:-}"
REGION="${2:-}"
STAGE="${3:-unknown}"

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  echo "[notify-failure] Skipping Telegram notification (missing secrets)" >&2
  exit 0
fi

# Metadata captured earlier if available
SUBJECT="$(cat .cb_commit_subject 2>/dev/null || true)"; SUBJECT=${SUBJECT:-""}
AUTHOR="$(cat .cb_commit_author 2>/dev/null || true)"; AUTHOR=${AUTHOR:-""}
BRANCH="$(cat .cb_branch 2>/dev/null || true)"; BRANCH=${BRANCH:-"${BRANCH_NAME:-main}"}
SHORT="${SHORT_SHA:-${COMMIT_SHA:-${REVISION_ID:-unknown}}}"

# Build log URL
PRJ="${PROJECT_ID:-}"
BLD="${BUILD_ID:-}"
LOG_URL="https://console.cloud.google.com/cloud-build/builds;region=${REGION}/${BLD}?project=${PRJ}"

TEXT="❌ Deploy failed ${SERVICE} (${SHORT})\nStage: ${STAGE}\nBranch: ${BRANCH}\nLogs: ${LOG_URL}"
if [[ -n "$SUBJECT" ]]; then TEXT+="\n${SUBJECT}"; fi
if [[ -n "$AUTHOR" ]]; then TEXT+="\nAuthor: ${AUTHOR}"; fi

curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${TELEGRAM_CHAT_ID}" \
  --data-urlencode text="${TEXT}" || true

