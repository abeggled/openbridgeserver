#!/usr/bin/env bash
# Build open bridge server artifacts locally.
# Only requirement: Docker.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BUILDER_IMAGE="obs-lxc-builder:latest"

# ── Defaults ───────────────────────────────────────────────────────────────────
VERSION=""
IMAGE_NAME="localhost/openbridgeserver"
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
  clean     Remove dist/ artifacts, rootfs cache, and builder image

Options:
  --version VER    Override version (default: git describe --tags --always --dirty)
  --image   NAME   Docker image name/prefix (default: localhost/openbridgeserver)
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

Notes:
  - The docker command passes the version via --build-arg OBS_VERSION so
    obs/version and gui/package.json in the working tree are never modified.
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
    local v
    v=$(git -C "$PROJECT_ROOT" describe --tags --always --dirty 2>/dev/null || echo "0.0.0-local")
    # git describe prefixes the commit hash with 'g' (e.g. 2026.6.0-RC4-22-ge633092-dirty).
    # Strip the 'g' so the hash segment matches `git rev-parse --short HEAD` exactly.
    echo "${v//-g/-}"
}

detect_repo() {
    local url result
    url=$(git -C "$PROJECT_ROOT" remote get-url origin 2>/dev/null || echo "")
    # Strip trailing .git, then extract the owner/repo suffix after github.com: or github.com/
    # Pure Bash — no sed, avoids BSD vs GNU sed ERE incompatibilities (non-greedy, etc.)
    url="${url%.git}"
    result="${url##*github.com[:/]}"   # remove everything up to and including github.com: or github.com/
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

# Locate the image that docker compose just built for the obs service.
# Docker Compose names images as <project>-<service> (v2) or <project>_<service> (legacy);
# try both so the result is independent of the Compose version.
compose_image_tag() {
    local project_name
    project_name=$(cd "$PROJECT_ROOT" && docker compose config 2>/dev/null | awk '/^name:/{print $2; exit}')
    local hyphen="${project_name}-obs:latest"
    local underscore="${project_name}_obs:latest"
    if docker image inspect "$hyphen" &>/dev/null; then
        echo "$hyphen"
    elif docker image inspect "$underscore" &>/dev/null; then
        echo "$underscore"
    else
        echo "error: could not find compose-built image (tried $hyphen and $underscore)" >&2
        exit 1
    fi
}

# ── Build functions ────────────────────────────────────────────────────────────
build_docker() {
    local version="$1"
    require_docker
    echo "==> Building Docker image ${IMAGE_NAME}:${version}..."

    # Derive the stamped obs/version string from RELEASENOTES.md + optional RC suffix.
    # This is passed as a build-arg so the working tree is never modified.
    local base rc obs_version
    base=$(grep -m1 '^## ' "$PROJECT_ROOT/RELEASENOTES.md" | sed 's/^## *//')
    if [[ "$version" =~ (-RC[0-9]*)$ ]]; then rc="${BASH_REMATCH[1]}"; else rc=""; fi
    obs_version="${base}${rc}"

    # Build via docker compose — passes OBS_VERSION build-arg into the Dockerfile
    docker compose --project-directory "$PROJECT_ROOT" build \
        --build-arg OBS_VERSION="$obs_version" obs

    # Retag the compose-built image with versioned names and remove the compose tag
    local githash dirty githash_tag src_image
    githash=$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "local")
    dirty=$(git -C "$PROJECT_ROOT" status --porcelain 2>/dev/null)
    githash_tag="${githash}$([[ -n "$dirty" ]] && echo "-dirty" || true)"
    src_image=$(compose_image_tag)

    docker tag "$src_image" "${IMAGE_NAME}:${version}"
    docker tag "$src_image" "${IMAGE_NAME}:${githash_tag}"
    # Remove the intermediate compose-named tag (keep only our versioned tags)
    docker image rm "$src_image" 2>/dev/null || true
    echo "==> Tagged: ${IMAGE_NAME}:${version}, ${IMAGE_NAME}:${githash_tag}"

    if [[ "$PUSH" == "true" ]]; then
        docker push "${IMAGE_NAME}:${version}"
        docker push "${IMAGE_NAME}:${githash_tag}"
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

build_clean() {
    echo "==> Cleaning build artifacts..."

    local removed=0

    # dist/ artifacts
    if [[ -d "$OUTPUT_DIR" ]]; then
        local files
        files=$(find "$OUTPUT_DIR" -maxdepth 1 \( -name "*.tar.zst" -o -name "*.tar.gz" -o -name "*.sha256" -o -name "*.sha512" \) 2>/dev/null)
        if [[ -n "$files" ]]; then
            echo "$files" | xargs rm -f
            echo "    Removed artifacts from $OUTPUT_DIR/"
            removed=1
        fi
    fi

    # Rootfs cache
    local cache_dir="$HOME/.cache/obs-lxc-builder"
    if compgen -G "$cache_dir/*.tar.zst" &>/dev/null; then
        rm -f "$cache_dir"/*.tar.zst
        echo "    Removed rootfs cache from $cache_dir/"
        removed=1
    fi

    # Builder image
    if docker image inspect "$BUILDER_IMAGE" &>/dev/null 2>&1; then
        docker image rm "$BUILDER_IMAGE"
        echo "    Removed builder image $BUILDER_IMAGE"
        removed=1
    fi

    [[ "$removed" -eq 0 ]] && echo "    Nothing to clean."
    echo "==> Done."
}

# ── Argument parsing ───────────────────────────────────────────────────────────
COMMAND=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        docker|lxc|bundle|all|clean)  COMMAND="$1"; shift ;;
        --version)                    VERSION="$2"; shift 2 ;;
        --image)                      IMAGE_NAME="$2"; shift 2 ;;
        --push)                       PUSH=true; shift ;;
        --repo)                       REPO="$2"; shift 2 ;;
        --output)                     OUTPUT_DIR="$2"; shift 2 ;;
        --no-cache)                   NO_CACHE=true; shift ;;
        -h|--help)                    usage; exit 0 ;;
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

# clean needs no version/repo resolution
if [[ "$COMMAND" == "clean" ]]; then
    require_docker
    build_clean
    exit 0
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
