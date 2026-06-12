#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

BASE_REF="${I18N_DIFF_BASE:-}"
HEAD_REF="${I18N_DIFF_HEAD:-}"

# Two diff-scoped gates run back-to-back; both must pass:
#   - check_i18n_guard.py:    frontend (gui/src, frontend/src) hardcoded strings + locale parity
#   - check_adapter_i18n.py:  backend adapter status/test strings must use locale codes (issue #779)
declare -a diff_args=()
if [[ -n "$BASE_REF" && -n "$HEAD_REF" ]]; then
  diff_args=(--base "$BASE_REF" --head "$HEAD_REF")
fi

status=0
python3 "$SCRIPT_DIR/check_i18n_guard.py" "${diff_args[@]}" "$@" || status=1
python3 "$SCRIPT_DIR/check_adapter_i18n.py" "${diff_args[@]}" "$@" || status=1
exit "$status"
