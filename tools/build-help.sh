#!/usr/bin/env bash
# Builds help_dist/ from help/ (a separate VitePress project, not built by any
# Python-side step). Unlike gui/ and frontend/, the help site has no live dev
# server wired into the Admin-GUI's proxy — it's built once into a static
# directory that obs/main.py mounts at /help on backend startup. Run this
# before starting the backend (see .run/OBS Backend.run.xml's "before launch"
# step) or standalone to refresh help content into an already-running backend.
set -e
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
cd "$(dirname "$0")/../help"
exec npm run build
