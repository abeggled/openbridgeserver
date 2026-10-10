#!/bin/bash
# SPDX-FileCopyrightText: 2026 abeggled and all contributors
# SPDX-License-Identifier: MIT
#
# build-migration-patch-bundle.sh — builds the migration patch release for the
# placeholder repo that stays behind after the organisation move (#1322).
#
# Old obs-update scripts query the API without -L, so they cannot follow the
# redirect of a transferred repo. The placeholder repo under the old path ends
# that redirect and offers exactly one release whose bundle only replaces
# obs-update (pointing to the new repo) and obs-admin. It carries no obs/ and
# no requirements.txt, so the app code stays untouched.
#
# The release meets what every obs-update variant since 2026.5.x expects:
#   - tag in strict version format, taken from MIGRATION_PATCH_TAG in obs-update
#   - asset name containing "app-bundle" and ending in .tar.gz
#   - SHA-256 in the release body, plus .sha256 and .sha512 sidecars
#   - no prerelease, no draft
#
# Usage:
#   tools/build-migration-patch-bundle.sh --repo OWNER/NAME --ops-repo OWNER/NAME \
#       [--out DIR] [--publish PLACEHOLDER_OWNER/NAME]
#
#   --repo      repo the patched obs-update fetches releases from (the new home)
#   --ops-repo  ops repo for the --channel mode of the patched obs-update
#   --out       output directory (default: dist/migration-patch)
#   --publish   create the release in this repo via `gh release create`;
#               the repo needs at least one commit (e.g. the README)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
REPO=""
OPS_REPO=""
OUT="$ROOT/dist/migration-patch"
PUBLISH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --ops-repo) OPS_REPO="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --publish) PUBLISH="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [[ ! "$REPO" =~ ^[^/]+/[^/]+$ || ! "$OPS_REPO" =~ ^[^/]+/[^/]+$ ]]; then
    echo "Error: --repo and --ops-repo are required as OWNER/NAME." >&2
    exit 2
fi

TAG=$(sed -n 's/^MIGRATION_PATCH_TAG="\(.*\)"$/\1/p' "$ROOT/scripts/obs-update")
if [[ ! "$TAG" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: could not read a strict MIGRATION_PATCH_TAG from scripts/obs-update." >&2
    exit 1
fi

BUNDLE="openbridgeserver-app-bundle_${TAG}.tar.gz"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

sed -e "s|__REPO__|${REPO}|g" -e "s|__OPS_REPO__|${OPS_REPO}|g" \
    "$ROOT/scripts/obs-update" > "$STAGE/obs-update.body"

# Landing pad for in-place self-updates. obs-update 2026.4.5–2026.6.1 replaces
# itself with `cp` while it is still running. bash reads a script piecewise and
# continues at the same byte offset, now in the new file: after the cp at byte
# 3081 (2026.4.5–2026.5.2) or 3643 (2026.6.x) for abeggled/openbridgeserver,
# a few bytes less for shorter repo names. Without a pad the old process parses
# arbitrary code there, aborts, and leaves the service stopped.
# Every suffix of a ": : :" line is a valid no-op, so the old process runs
# harmlessly into the hand-off block, which finishes its job (start the
# service, but keep the real version file) and exits. A fresh run skips the
# block because TARGET and SERVICE are not set yet.
PAD_END=4608
{
    head -n 1 "$STAGE/obs-update.body"
    echo "# Migration patch for the organisation move (#1322), see"
    echo "# tools/build-migration-patch-bundle.sh for the landing pad below."
    PAD_LINE=": : : : : : : : : : : : : : : : : : : : : : : : : : : : : : : : : : : : : : :"
    : > "$STAGE/obs-update.pad"
    while [[ $(wc -c < "$STAGE/obs-update.pad") -lt $PAD_END ]]; do
        echo "$PAD_LINE" >> "$STAGE/obs-update.pad"
    done
    cat "$STAGE/obs-update.pad"
    cat <<EOF
if [[ -n "\${SERVICE:-}" && "\${TARGET:-}" == "${TAG}" ]]; then
    rm -f "\${INSTALL_DIR:-/opt/obs}/obs-update"
    systemctl start "\$SERVICE"
    echo "Done. obs-update now fetches releases from ${REPO}."
    echo "Run obs-update again to see them."
    exit 0
fi
EOF
    tail -n +2 "$STAGE/obs-update.body"
} > "$STAGE/obs-update"
rm -f "$STAGE/obs-update.body" "$STAGE/obs-update.pad"

PAD_START=$(grep -bm1 '^: : :' "$STAGE/obs-update" | cut -d: -f1)
if [[ "$PAD_START" -ge 3000 ]]; then
    echo "Error: landing pad starts at byte $PAD_START, after the oldest resume offset." >&2
    exit 1
fi

install -m 755 "$ROOT/scripts/obs-admin" "$STAGE/obs-admin"
chmod 755 "$STAGE/obs-update"

if grep -q '__REPO__\|__OPS_REPO__' "$STAGE/obs-update"; then
    echo "Error: unreplaced placeholder in obs-update." >&2
    exit 1
fi

mkdir -p "$OUT"
rm -f "$OUT/$BUNDLE" "$OUT/$BUNDLE.sha256" "$OUT/$BUNDLE.sha512" "$OUT/release-notes.md"
tar -czf "$OUT/$BUNDLE" --owner=0 --group=0 -C "$STAGE" obs-update obs-admin
(
    cd "$OUT"
    sha256sum "$BUNDLE" > "$BUNDLE.sha256"
    sha512sum "$BUNDLE" > "$BUNDLE.sha512"
)
SHA256=$(cut -d' ' -f1 "$OUT/$BUNDLE.sha256")

# The checksum block must match the format obs-update parses from the body:
# a line containing "app-bundle", then a fenced block holding the hash.
cat > "$OUT/release-notes.md" <<EOF
**This repository has moved to https://github.com/${REPO}.**

This release only updates \`obs-update\` so that it fetches releases from the new
location. Your installed version stays the same. After installing it, run
\`obs-update\` once more to see the current releases.

**Dieses Repository ist umgezogen nach https://github.com/${REPO}.**

Dieses Release aktualisiert nur \`obs-update\`, damit es die Releases vom neuen
Ort holt. Die installierte Version bleibt unverändert. Danach \`obs-update\`
ein zweites Mal starten, um die aktuellen Releases zu sehen.

### Checksums (SHA-256)

**App Bundle**

*${BUNDLE}*:
\`\`\`
${SHA256}
\`\`\`
EOF

echo "Built $OUT/$BUNDLE (tag $TAG, obs-update -> $REPO)"

if [[ -n "$PUBLISH" ]]; then
    gh release create "$TAG" -R "$PUBLISH" \
        --title "$TAG – moved to ${REPO}" \
        --notes-file "$OUT/release-notes.md" \
        --latest \
        "$OUT/$BUNDLE" "$OUT/$BUNDLE.sha256" "$OUT/$BUNDLE.sha512"
    echo "Published $TAG in $PUBLISH"
fi
