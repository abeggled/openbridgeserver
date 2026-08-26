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
# help/ has its own package.json, separate from gui/'s — a checkout that only
# ran the documented `cd gui && npm install` one-time step (help/ isn't
# mentioned there) fails here with "vitepress: not found" otherwise, breaking
# this script's use as a PyCharm "before launch" step on a fresh clone
# (Codex review on PR #1180).
[ -x node_modules/.bin/vitepress ] || npm install
exec npm run build
