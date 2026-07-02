#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

BASE_REF="${I18N_DIFF_BASE:-}"
HEAD_REF="${I18N_DIFF_HEAD:-}"

status=0
# Branch explicitly instead of expanding an empty array: macOS Bash 3.2 treats
# "${array[@]}" as unbound under set -u when the array has no elements.
if [[ -n "$BASE_REF" && -n "$HEAD_REF" ]]; then
  python3 "$SCRIPT_DIR/check_i18n_guard.py" --base "$BASE_REF" --head "$HEAD_REF" "$@" || status=1
  python3 "$SCRIPT_DIR/check_adapter_i18n.py" --base "$BASE_REF" --head "$HEAD_REF" "$@" || status=1
else
  python3 "$SCRIPT_DIR/check_i18n_guard.py" "$@" || status=1
  python3 "$SCRIPT_DIR/check_adapter_i18n.py" "$@" || status=1
fi
exit "$status"
