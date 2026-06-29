#!/usr/bin/env bash
# Sync the canonical brand logos into the per-app Vite public directories.
#
# Source of truth:  logo/obs_logo_{light,dark}.svg
# Deploy copies:    gui/public/      (Admin GUI)
#                   frontend/public/ (Visu SPA)
#
# The two Vite apps build independently and cannot share a public asset, so the
# logos are duplicated into each public dir. Edit the SVGs in logo/ only, then
# run this script to propagate. Run it after any logo refactor.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/logo"
LOGOS=(obs_logo_light.svg obs_logo_dark.svg)
TARGETS=("$ROOT/gui/public" "$ROOT/frontend/public")

for logo in "${LOGOS[@]}"; do
  if [[ ! -f "$SRC/$logo" ]]; then
    echo "error: missing source $SRC/$logo" >&2
    exit 1
  fi
done

for target in "${TARGETS[@]}"; do
  mkdir -p "$target"
  for logo in "${LOGOS[@]}"; do
    cp "$SRC/$logo" "$target/$logo"
    echo "synced $logo -> ${target#"$ROOT"/}/$logo"
  done
done
