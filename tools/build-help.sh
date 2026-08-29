#!/usr/bin/env bash
# Builds help_dist/ from help/ (a separate VitePress project, not built by any
# Python-side step). Unlike gui/ and frontend/, the help site has no live dev
# server wired into the Admin-GUI's proxy — it's built once into a static
# directory that obs/main.py mounts at /help. The backend's own lazy /help
# mount (_LazyHelpStatic) re-checks help_dist/ on every request, so this can
# run before, after, or entirely independently of the backend starting — run
# it standalone any time to (re)build or refresh help content.
set -e
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
cd "$(dirname "$0")/../help"
# vitepress build writes per-page SSR modules into a shared .vitepress/.temp/
# scratch directory that isn't safe for two concurrent builds — one process's
# cleanup/overwrite can delete a file the other is mid-import on, failing
# with a misleading ERR_MODULE_NOT_FOUND instead of a clear error. This bit
# a user whose "OBS Full Dev Stack" compound ran this script both as its own
# member AND via OBS Backend's (now-removed, and redundant now that the lazy
# mount above makes it unnecessary) "before launch" step — two builds firing
# at once. flock serializes any future concurrent invocation instead.
exec 200>.build.lock
flock 200
# help/ has its own package.json, separate from gui/'s — a checkout that only
# ran the documented `cd gui && npm install` one-time step (help/ isn't
# mentioned there) fails here with "vitepress: not found" otherwise, breaking
# this script's use as a PyCharm run configuration on a fresh clone (Codex
# review on PR #1180).
[ -x node_modules/.bin/vitepress ] || npm install
exec npm run build
