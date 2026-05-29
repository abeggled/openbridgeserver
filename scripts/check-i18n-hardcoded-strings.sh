#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

BASE_REF="${I18N_DIFF_BASE:-}"
HEAD_REF="${I18N_DIFF_HEAD:-}"

if [[ -n "$BASE_REF" && -n "$HEAD_REF" ]]; then
  exec python3 "$SCRIPT_DIR/check_i18n_guard.py" --base "$BASE_REF" --head "$HEAD_REF" "$@"
fi

exec python3 "$SCRIPT_DIR/check_i18n_guard.py" "$@"
