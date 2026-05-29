#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

MODE="${1:---check}"

case "${MODE}" in
  --check)
    echo "Running CI-parity lint checks (ruff check + ruff format --check)"
    python3 -m ruff check .
    python3 -m ruff format . --check
    ;;
  --fix)
    echo "Running autofix lint (ruff check --fix + ruff format)"
    python3 -m ruff check . --fix
    python3 -m ruff format .
    ;;
  *)
    echo "Usage: $(basename "$0") [--check|--fix]" >&2
    exit 2
    ;;
esac
