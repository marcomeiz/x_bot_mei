#!/usr/bin/env bash
set -euo pipefail

# Capture subject and author of the last commit
git log -1 --pretty=%s > .cb_commit_subject || echo "" > .cb_commit_subject
git log -1 --pretty=%an > .cb_commit_author || echo "" > .cb_commit_author

# Determine branch name: prefer git, fallback to Cloud Build env BRANCH_NAME
BR=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)
if [ -z "$BR" ]; then BR="${BRANCH_NAME:-main}"; fi
echo "$BR" > .cb_branch

