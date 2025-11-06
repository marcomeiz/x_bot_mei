#!/usr/bin/env bash
set -uo pipefail
# Note: removed -e to allow script to continue on errors

# Use environment variables passed from GitHub Actions
# Fallback to git commands if not available (for manual builds)
if [ -n "${COMMIT_SUBJECT:-}" ]; then
  echo "$COMMIT_SUBJECT" > .cb_commit_subject || echo "Unknown commit" > .cb_commit_subject
else
  git log -1 --pretty=%s > .cb_commit_subject 2>/dev/null || echo "Unknown commit" > .cb_commit_subject
fi

if [ -n "${COMMIT_AUTHOR:-}" ]; then
  echo "$COMMIT_AUTHOR" > .cb_commit_author || echo "Unknown author" > .cb_commit_author
else
  git log -1 --pretty=%an > .cb_commit_author 2>/dev/null || echo "Unknown author" > .cb_commit_author
fi

# Determine branch name: prefer env var, fallback to git
BR="${BRANCH_NAME:-}"
if [ -z "$BR" ]; then
  BR=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
fi
echo "$BR" > .cb_branch || echo "main" > .cb_branch

# Always exit successfully - this is just metadata gathering
exit 0
