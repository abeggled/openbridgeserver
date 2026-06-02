#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git repository root: ${ROOT_DIR}" >&2
  exit 1
fi

HOOK_FILE=".githooks/pre-push"
if [ ! -f "${HOOK_FILE}" ]; then
  echo "Missing hook file: ${HOOK_FILE}" >&2
  exit 1
fi

chmod +x "${HOOK_FILE}"
git config core.hooksPath .githooks

echo "Git hooks enabled."
echo "core.hooksPath=$(git config --get core.hooksPath)"
echo "Hook executable: ${HOOK_FILE}"
