#!/usr/bin/env bash

set -euo pipefail

# Resolve project root relative to this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Optionally load variables from .env if present
ENV_FILE="${REPO_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "${ENV_FILE}"
  set +a
fi

: "${NOTION_API_TOKEN:?NOTION_API_TOKEN no está definido. Expórtalo antes de ejecutar este script.}"
: "${NOTION_DATABASE_ID:?NOTION_DATABASE_ID no está definido. Expórtalo antes de ejecutar este script.}"

PROMOTE_STATUS="${NOTION_PROMOTE_STATUS:-Validated}"
TARGET_STATUS="${NOTION_PROMOTE_SET_STATUS:-Promoted}"
SYNC_FIELD="${NOTION_PROMOTE_SYNC_FIELD:-Synced}"

LOG_DIR="${REPO_DIR}/logs"
mkdir -p "${LOG_DIR}"

VENV_BIN="${REPO_DIR}/venv/bin"
if [[ ! -x "${VENV_BIN}/python" ]]; then
  echo "No se encontró ${VENV_BIN}/python. Crea el entorno virtual antes de usar este script." >&2
  exit 1
fi

source "${VENV_BIN}/activate"

timestamp() {
  date +"%Y-%m-%d %H:%M:%S"
}

{
  echo "[$(timestamp)] Lanzando promote_notion_topics (status=${PROMOTE_STATUS} → ${TARGET_STATUS})"
  python "${REPO_DIR}/promote_notion_topics.py" \
    --token "${NOTION_API_TOKEN}" \
    --database "${NOTION_DATABASE_ID}" \
    --status "${PROMOTE_STATUS}" \
    --set-status "${TARGET_STATUS}" \
    --synced-property "${SYNC_FIELD}"
  echo "[$(timestamp)] Ejecución completada"
} >> "${LOG_DIR}/promote.log" 2>&1
