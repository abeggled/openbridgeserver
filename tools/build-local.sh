#!/usr/bin/env bash
# Build open bridge server artifacts locally.
# Only requirement: Docker.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BUILDER_IMAGE="obs-lxc-builder:latest"

# ── Defaults ───────────────────────────────────────────────────────────────────
VERSION=""
IMAGE_NAME="obs"
PUSH=false
REPO=""
OUTPUT_DIR="${PROJECT_ROOT}/dist"
NO_CACHE=false

# ── Usage ──────────────────────────────────────────────────────────────────────
usage() {
    cat << 'EOF'
Usage: tools/build-local.sh [OPTIONS] COMMAND

Build open bridge server artifacts locally. Only requirement: Docker.

Commands:
  docker    Build Docker image via docker compose build obs
  lxc       Build LXC .tar.zst template (runs inside Docker, needs --privileged)
  bundle    Build app bundle only (no rootfs, much faster than lxc)
  all       Build docker + lxc

Options:
  --version VER    Override version (default: git describe --tags --always --dirty)
  --image   NAME   Docker image name/prefix (default: obs)
                   For registry push: e.g. ghcr.io/owner/openbridgeserver
  --push           Push Docker image to registry after build
  --repo    REPO   GitHub repo slug for the obs-update script, e.g. owner/openbridgeserver
                   (default: auto-detected from git remote origin)
  --output  DIR    Output directory for LXC/bundle artifacts (default: dist/)
  --no-cache       Rebuild builder image without cache and skip the rootfs cache
  -h, --help       Show this help

Examples:
  tools/build-local.sh docker
  tools/build-local.sh --version 2026.6.0 lxc
  tools/build-local.sh --push --image ghcr.io/owner/openbridgeserver docker
  tools/build-local.sh --no-cache lxc
  tools/build-local.sh all

Notes:
  - The lxc and bundle commands use a builder Docker image (obs-lxc-builder) that is
    built automatically on first run and cached via Docker layer cache.
  - The debootstrap base system is cached in ~/.cache/obs-lxc-builder/ to speed up
    repeated lxc builds. Remove that directory or pass --no-cache to rebuild from scratch.
  - Cross-arch LXC builds are not supported locally; the output arch matches the host.
  - For multi-arch Docker builds, ensure QEMU binfmts are registered first:
      docker run --privileged --rm tonistiigi/binfmt --install all
EOF
}

# ── Helpers ────────────────────────────────────────────────────────────────────
detect_version() {
    git -C "$PROJECT_ROOT" describe --tags --always --dirty 2>/dev/null || echo "0.0.0-local"
}

detect_repo() {
    local url result
    url=$(git -C "$PROJECT_ROOT" remote get-url origin 2>/dev/null || echo "")
    result=$(echo "$url" | sed -E 's|.*github\.com[:/]([^/]+/[^/]+?)(\.git)?$|\1|')
    if [[ "$result" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
        echo "$result"
    else
        echo ""
    fi
}

require_docker() {
    if ! command -v docker &>/dev/null; then
        echo "error: docker not found — please install Docker" >&2
        exit 1
    fi
}

ensure_builder_image() {
    echo "==> Building LXC builder image (obs-lxc-builder)..."
    local no_cache_flag=()
    [[ "$NO_CACHE" == "true" ]] && no_cache_flag=(--no-cache)
    docker build "${no_cache_flag[@]}" \
        --tag "$BUILDER_IMAGE" \
        --file "$SCRIPT_DIR/Dockerfile.lxc-builder" \
        "$SCRIPT_DIR"
}

check_privileged() {
    if ! docker run --rm --privileged "$BUILDER_IMAGE" \
        /bin/bash -c "mount -t tmpfs tmpfs /tmp && umount /tmp" >/dev/null 2>&1; then
        echo "error: --privileged containers lack mount capability on this Docker setup." >&2
        echo "       The LXC build needs it for debootstrap and chroot mounts." >&2
        echo "       Likely cause: rootless Docker (check: docker info | grep -i rootless)." >&2
        exit 1
    fi
}

# ── Build functions ────────────────────────────────────────────────────────────
build_docker() {
    local version="$1"
    require_docker
    echo "==> Building Docker image ${IMAGE_NAME}:${version}..."

    # Stamp obs/version and gui/package.json; restore both on exit
    local orig_obs_version orig_pkg_json
    orig_obs_version=$(cat "$PROJECT_ROOT/obs/version")
    orig_pkg_json=$(cat "$PROJECT_ROOT/gui/package.json")
    restore_stamps() {
        echo "$orig_obs_version" > "$PROJECT_ROOT/obs/version"
        printf '%s\n' "$orig_pkg_json" > "$PROJECT_ROOT/gui/package.json"
    }
    trap restore_stamps EXIT

    local base rc
    base=$(grep -m1 '^## ' "$PROJECT_ROOT/RELEASENOTES.md" | sed 's/^## *//')
    rc=$(echo "$version" | grep -oP -- '-RC\d*$' || true)
    echo "${base}${rc}" > "$PROJECT_ROOT/obs/version"
    (command -v npm &>/dev/null && npm pkg set version="$version" --prefix "$PROJECT_ROOT/gui") || true

    # Build via docker compose — reuses compose context, Dockerfile, and .env build args
    docker compose --project-directory "$PROJECT_ROOT" build obs

    restore_stamps
    trap - EXIT

    # If a custom image name or push was requested, retag the compose-built image
    if [[ "$IMAGE_NAME" != "obs" ]] || [[ "$PUSH" == "true" ]]; then
        local githash project_name src_image
        githash=$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "local")
        project_name=$(cd "$PROJECT_ROOT" && docker compose config 2>/dev/null | awk '/^name:/{print $2; exit}')
        src_image="${project_name}-obs:latest"

        docker tag "$src_image" "${IMAGE_NAME}:${version}"
        docker tag "$src_image" "${IMAGE_NAME}:${githash}"
        if [[ "$PUSH" == "true" ]]; then
            docker push "${IMAGE_NAME}:${version}"
            docker push "${IMAGE_NAME}:${githash}"
        fi
        echo "==> Tagged: ${IMAGE_NAME}:${version}, ${IMAGE_NAME}:${githash}"
    fi

    echo "==> Docker image built successfully."
}

build_lxc() {
    local version="$1" repo="$2"
    require_docker
    ensure_builder_image
    check_privileged

    local cache_dir="$HOME/.cache/obs-lxc-builder"
    mkdir -p "$OUTPUT_DIR" "$cache_dir"
    echo "==> Building LXC template version=${version}..."

    docker run --rm --privileged \
        --env VERSION="$version" \
        --env REPO="$repo" \
        --env NO_CACHE="$NO_CACHE" \
        --volume "$PROJECT_ROOT:/workspace:ro" \
        --volume "$OUTPUT_DIR:/output" \
        --volume "$cache_dir:/cache" \
        --volume "$SCRIPT_DIR/_lxc-inner.sh:/build-lxc.sh:ro" \
        "$BUILDER_IMAGE" \
        /bin/bash /build-lxc.sh

    echo "==> LXC artifacts written to $OUTPUT_DIR"
}

build_bundle() {
    local version="$1" repo="$2"
    require_docker
    ensure_builder_image

    mkdir -p "$OUTPUT_DIR"
    echo "==> Building app bundle version=${version}..."

    docker run --rm \
        --env VERSION="$version" \
        --env REPO="$repo" \
        --env BUNDLE_ONLY="true" \
        --volume "$PROJECT_ROOT:/workspace:ro" \
        --volume "$OUTPUT_DIR:/output" \
        --volume "$SCRIPT_DIR/_lxc-inner.sh:/build-lxc.sh:ro" \
        "$BUILDER_IMAGE" \
        /bin/bash /build-lxc.sh

    echo "==> Bundle artifacts written to $OUTPUT_DIR"
}

# ── Argument parsing ───────────────────────────────────────────────────────────
COMMAND=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        docker|lxc|bundle|all)  COMMAND="$1"; shift ;;
        --version)              VERSION="$2"; shift 2 ;;
        --image)                IMAGE_NAME="$2"; shift 2 ;;
        --push)                 PUSH=true; shift ;;
        --repo)                 REPO="$2"; shift 2 ;;
        --output)               OUTPUT_DIR="$2"; shift 2 ;;
        --no-cache)             NO_CACHE=true; shift ;;
        -h|--help)              usage; exit 0 ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2 ;;
    esac
done

if [[ -z "$COMMAND" ]]; then
    echo "error: no command specified" >&2
    usage >&2
    exit 2
fi

# ── Resolve defaults ───────────────────────────────────────────────────────────
[[ -z "$VERSION" ]] && VERSION=$(detect_version)

if [[ -z "$REPO" ]]; then
    REPO=$(detect_repo)
    if [[ -z "$REPO" ]]; then
        echo "warning: could not auto-detect GitHub repo from git remote" >&2
        echo "         obs-update will use placeholder — pass --repo owner/repo to fix" >&2
        REPO="unknown/openbridgeserver"
    fi
fi

echo "Version : $VERSION"
[[ "$COMMAND" != "docker" ]] && echo "Repo    : $REPO"
[[ "$COMMAND" != "docker" ]] && echo "Output  : $OUTPUT_DIR"
echo ""

# ── Dispatch ───────────────────────────────────────────────────────────────────
case "$COMMAND" in
    docker)
        build_docker "$VERSION"
        ;;
    lxc)
        build_lxc "$VERSION" "$REPO"
        ;;
    bundle)
        build_bundle "$VERSION" "$REPO"
        ;;
    all)
        build_docker "$VERSION"
        build_lxc    "$VERSION" "$REPO"
        echo ""
        echo "==> All builds complete."
        echo "    Docker : ${IMAGE_NAME}:${VERSION}"
        echo "    LXC    : $OUTPUT_DIR"
        ;;
esac
