#!/usr/bin/env bash
set -euo pipefail

SERVICE="${1:-}"
REGION="${2:-}"

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  echo "[notify-success] Skipping Telegram notification (missing secrets)"
  exit 0
fi

RUN_URL=$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')
SHORT="${SHORT_SHA:-${COMMIT_SHA:-${REVISION_ID:-unknown}}}"
SUBJECT="$(cat .cb_commit_subject 2>/dev/null || true)"; SUBJECT=${SUBJECT:-""}

TEXT="✅ Deployed ${SERVICE} (${SHORT})\nURL: ${RUN_URL}"
if [[ -n "$SUBJECT" ]]; then TEXT+="\n${SUBJECT}"; fi

curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${TELEGRAM_CHAT_ID}" \
  --data-urlencode text="${TEXT}" || true

