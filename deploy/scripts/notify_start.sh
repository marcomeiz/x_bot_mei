#!/usr/bin/env bash
set -euo pipefail

SERVICE="${1:-}"
REGION="${2:-}"

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  echo "[notify-start] Skipping Telegram notification (missing secrets)"
  exit 0
fi

SHORT="${SHORT_SHA:-${COMMIT_SHA:-${REVISION_ID:-unknown}}}"
SUBJECT="$(cat .cb_commit_subject 2>/dev/null || true)"; SUBJECT=${SUBJECT:-""}
AUTHOR="$(cat .cb_commit_author 2>/dev/null || true)"; AUTHOR=${AUTHOR:-""}
BRANCH="$(cat .cb_branch 2>/dev/null || true)"; BRANCH=${BRANCH:-"main"}

TEXT="🚀 Deploying ${SERVICE} → ${REGION}\nBranch: ${BRANCH}\nCommit: ${SHORT}"
if [[ -n "$SUBJECT" ]]; then TEXT+="\nSubject: ${SUBJECT}"; fi
if [[ -n "$AUTHOR" ]]; then TEXT+="\nAuthor: ${AUTHOR}"; fi

curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${TELEGRAM_CHAT_ID}" \
  --data-urlencode text="${TEXT}"

