#!/usr/bin/env bash
# Inner Matter LXC build script — runs as root inside the obs-lxc-builder container.
# Invoked by tools/build-local.sh (matter-lxc command); not intended to be run directly.
#
# Builds a standalone Proxmox LXC template that runs the official `matterbridge` npm
# package plus our matterbridge-obs plugin (matter/) as a systemd service. Deliberately
# NOT embedded into the main openbridgeserver-lxc template — see issue #56 / discussion
# #357 for why (avoids a permanent Node.js footprint on every OBS install). This template
# only talks to an existing OBS instance over the network (MQTT + REST), exactly like the
# already-verified Docker sidecar in docker-compose.yml.
set -euo pipefail

VERSION="${VERSION:-0.0.0-local}"
NO_CACHE="${NO_CACHE:-false}"

ARCH=$(dpkg --print-architecture)
TEMPLATE_NAME="matterbridge-obs"
TEMPLATE_FILE="${TEMPLATE_NAME}-lxc_${VERSION}_${ARCH}.tar.zst"
# Shares the same base rootfs *content* as the main LXC template (plain Ubuntu Resolute,
# nothing OBS-specific in it) but its own cache file/dir, to avoid the two builders racing
# on the same cache entry when run back-to-back.
CACHE_FILE="/cache/base-system-ubuntu-resolute-${ARCH}.tar.zst"
ROOTFS="/tmp/rootfs"

if [[ "$ARCH" == "arm64" ]]; then
    MIRROR="http://ports.ubuntu.com/ubuntu-ports"
    SECURITY_MIRROR="http://ports.ubuntu.com/ubuntu-ports"
else
    MIRROR="http://archive.ubuntu.com/ubuntu"
    SECURITY_MIRROR="http://security.ubuntu.com/ubuntu"
fi

# ── Prepare build directory ───────────────────────────────────────────────────
echo "==> Preparing build directory..."
mkdir -p /build
cp -r /workspace/matter /build/matter
rm -rf /build/matter/node_modules /build/matter/dist /build/matter/coverage
cd /build/matter

# ── Build the plugin ───────────────────────────────────────────────────────────
# Rebuilt fresh inside the container rather than trusting a host-built dist/, avoiding
# any host/container Node version skew.
#
# tsc needs a real `matterbridge` package to resolve the plugin's SDK imports — same
# requirement as local dev (see matter/README.md). This builder container is ephemeral
# (`docker run --rm`) and its node_modules are never copied into the target rootfs (that
# gets its own fresh `npm ci --omit=dev` inside the chroot below), so there's no
# stray-symlink leak to worry about here, unlike the Docker sidecar's bind mount.
echo "==> Installing matterbridge for local type-checking..."
npm install -g matterbridge --no-fund --no-audit

echo "==> Building matterbridge-obs plugin..."
npm ci
npm link --no-fund --no-audit matterbridge
npm run build

PLUGIN_VERSION=$(node -p "require('./package.json').version")
echo "==> matterbridge-obs plugin version: $PLUGIN_VERSION (shown as-is in the Matterbridge"
echo "    frontend's Plugins table — independent of this template's own \$VERSION, which"
echo "    only identifies the git checkout this particular build came from)."

# ── Base system (debootstrap or restore from cache) ───────────────────────────
mkdir -p "$ROOTFS"

if [[ "$NO_CACHE" != "true" ]] && [[ -f "$CACHE_FILE" ]]; then
    echo "==> Restoring base system from cache (~/.cache/matter-lxc-builder)..."
    tar --zstd -xf "$CACHE_FILE" -C "$ROOTFS"
else
    echo "==> Running debootstrap for resolute/${ARCH} (this may take several minutes)..."
    # Ubuntu releases all share the generic 'gutsy' debootstrap script.
    # The builder image's debootstrap may predate resolute (26.04); symlink it if missing.
    if [[ ! -e /usr/share/debootstrap/scripts/resolute ]]; then
        ln -sf gutsy /usr/share/debootstrap/scripts/resolute
    fi
    debootstrap \
        --arch="$ARCH" \
        --components=main,restricted,universe \
        --include=systemd,systemd-sysv,dbus,apt-utils,locales,iproute2,wget,curl,ca-certificates,less,logrotate,openssh-server,ifupdown \
        resolute \
        "$ROOTFS" \
        "$MIRROR"

    tee "$ROOTFS/etc/apt/sources.list" > /dev/null << SOURCES
deb $MIRROR resolute main restricted universe multiverse
deb $MIRROR resolute-updates main restricted universe multiverse
deb $SECURITY_MIRROR resolute-security main restricted universe multiverse
SOURCES

    chroot "$ROOTFS" /bin/bash << 'BASESCRIPT'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "en_US.UTF-8 UTF-8" >> /etc/locale.gen
locale-gen
update-locale LANG=en_US.UTF-8

ln -sf /usr/share/zoneinfo/UTC /etc/localtime
echo "UTC" > /etc/timezone

cat > /etc/network/interfaces << 'EOF'
auto lo
iface lo inet loopback
EOF

echo "localhost" > /etc/hostname

cat > /etc/hosts << 'EOF'
127.0.0.1   localhost
::1         localhost ip6-localhost ip6-loopback
ff02::1     ip6-allnodes
ff02::2     ip6-allrouters
EOF

echo "# Proxmox manages container mounts" > /etc/fstab

mkdir -p /etc/systemd/system-preset
echo "disable systemd-networkd-wait-online.service" \
    > /etc/systemd/system-preset/00-pve-template.preset

apt-get clean
rm -rf /var/lib/apt/lists/*
BASESCRIPT

    echo "==> Saving base system to cache..."
    tar --zstd -cf "$CACHE_FILE" -C "$ROOTFS" .
fi

# ── Install matterbridge + matterbridge-obs ─────────────────────────────────────
echo "==> Installing matterbridge + matterbridge-obs into rootfs..."
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"

mount -t proc proc "$ROOTFS/proc"
trap 'mountpoint -q "$ROOTFS/proc" && umount "$ROOTFS/proc" || true' EXIT

# Same directory layout as the already-verified Docker sidecar
# (docker-compose.yml's matterbridge_storage/matterbridge_cert volumes), just for root
# on bare metal instead of container volumes.
mkdir -p "$ROOTFS/root/Matterbridge/matterbridge-obs"
cp -r dist package.json package-lock.json matterbridge-obs.config.json matterbridge-obs.schema.json \
    "$ROOTFS/root/Matterbridge/matterbridge-obs/"

chroot "$ROOTFS" /bin/bash << 'INSTALL'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends gnupg
curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
apt-get install -y nodejs

npm install -g matterbridge --omit=dev --no-fund --no-audit

mkdir -p /root/.matterbridge /root/.mattercert

# Real runtime deps only (mqtt) — no dev tooling, reproducible via the committed lockfile.
cd /root/Matterbridge/matterbridge-obs
npm ci --omit=dev --no-fund --no-audit

# No cross-arch emulation involved (the CI matrix uses a native arm64 runner, and local
# builds target the host's own arch), so plugin registration can run for real at build
# time — no first-boot registration step needed, unlike OBS's MQTT credential generation.
matterbridge --add /root/Matterbridge/matterbridge-obs

# Modeled on Matterbridge's own documented systemd unit (README-SERVICE.md in the npm
# package), substituting root/`/root` for the vendor doc's `<USER>`/`/home/<USER>` — this
# LXC template runs matterbridge as root, matching this project's existing appliance-style
# convention (obs.service has no dedicated service user either).
cat > /etc/systemd/system/matterbridge.service << 'EOF'
[Unit]
Description=matterbridge
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart=matterbridge --service
WorkingDirectory=/root/Matterbridge
# Vendor-documented workaround for a real, recurring issue: Node.js may prefer an IPv6
# route that doesn't actually work, causing ENETUNREACH on npm/network operations.
Environment="NODE_OPTIONS=--dns-result-order=ipv4first"
StandardOutput=journal
StandardError=journal
SyslogIdentifier=matterbridge
Restart=always
RestartSec=5
TimeoutStopSec=60
User=root
Group=root

[Install]
WantedBy=multi-user.target
EOF

systemctl enable matterbridge.service

apt-get clean
rm -rf /var/lib/apt/lists/*
INSTALL

# Unmount proc now — before finalization and packaging.
# The EXIT trap would fire too late (after tar), causing "Permission denied" on
# /proc/sys pseudo-files and xattr warnings from still-mounted proc sub-mounts.
umount "$ROOTFS/proc"
trap - EXIT

# ── Finalize ───────────────────────────────────────────────────────────────────
echo "==> Finalizing rootfs..."
chroot "$ROOTFS" /bin/bash << 'CLEANUP'
set -euo pipefail
truncate -s 0 /etc/machine-id
rm -f /etc/ssh/ssh_host_*
find /var/log -type f -exec truncate -s 0 {} \;
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
rm -f /root/.bash_history
truncate -s 0 /etc/resolv.conf
CLEANUP

# ── Package ────────────────────────────────────────────────────────────────────
echo "==> Packaging as $TEMPLATE_FILE..."
cd "$ROOTFS"
tar --numeric-owner --acls --xattrs \
    -cf - . | zstd -T0 -9 -o "/tmp/$TEMPLATE_FILE"
cd /tmp
sha256sum "$TEMPLATE_FILE" > "$TEMPLATE_FILE.sha256"
cp "$TEMPLATE_FILE" "$TEMPLATE_FILE.sha256" /output/

echo ""
echo "==> Done! Artifacts:"
ls -lh /output/
