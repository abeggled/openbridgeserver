#!/usr/bin/env bash
# Inner LXC build script — runs as root inside the obs-lxc-builder container.
# Invoked by tools/build-local.sh; not intended to be run directly.
set -euo pipefail

VERSION="${VERSION:-0.0.0-local}"
REPO="${REPO:-unknown/openbridgeserver}"
BUNDLE_ONLY="${BUNDLE_ONLY:-false}"
NO_CACHE="${NO_CACHE:-false}"

ARCH=$(dpkg --print-architecture)
TEMPLATE_NAME="openbridgeserver"
TEMPLATE_FILE="${TEMPLATE_NAME}-lxc_${VERSION}_${ARCH}.tar.zst"
APP_BUNDLE_FILE="${TEMPLATE_NAME}-app-bundle_${VERSION}.tar.gz"
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
cp -r /workspace/. /build/
rm -rf /build/gui/node_modules /build/frontend/node_modules /build/.venv
cd /build

# ── Stamp version ─────────────────────────────────────────────────────────────
BASE=$(grep -m1 '^## ' RELEASENOTES.md | sed 's/^## *//')
RC=$(echo "$VERSION" | grep -oP -- '-RC\d*$' || true)
echo "${BASE}${RC}" > obs/version
npm pkg set version="$VERSION" --prefix gui

# ── Build frontends ───────────────────────────────────────────────────────────
echo "==> Building Admin GUI..."
cd gui
npm install --prefer-offline
npm run build
cd ..

echo "==> Building Visu frontend..."
cd frontend
npm install --prefer-offline
npm run build
cd ..

# ── Write obs-update ───────────────────────────────────────────────────────────
cat > /tmp/obs-update << 'SCRIPTEOF'
#!/bin/bash
set -euo pipefail

REPO="__REPO__"
INSTALL_DIR="/opt/obs"
SERVICE="obs"

CURRENT=$(cat "$INSTALL_DIR/version" 2>/dev/null || echo "unknown")
echo "Fetching release information..."
ALL_RELEASES=$(curl -sf "https://api.github.com/repos/${REPO}/releases")

# Build ordered list via semantic version sort, stopping after 2 stables
VERSION_LIST=$(echo "$ALL_RELEASES" | python3 -c "
import sys, json, re

releases = json.load(sys.stdin)

def sort_key(tag):
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)(?:-RC(\d+))?\$', tag, re.IGNORECASE)
    if not m:
        return (0, 0, 0, 0, 0)
    y, mn, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
    rc = int(m.group(4)) if m.group(4) else None
    return (y, mn, p, 1 if rc is None else 0, rc or 0)

candidates = sorted(
    [r for r in releases if not r['draft']],
    key=lambda r: sort_key(r['tag_name']), reverse=True
)

stables_seen = 0
for r in candidates:
    kind = 'rc' if r['prerelease'] else 'stable'
    print(kind + ' ' + r['tag_name'])
    if not r['prerelease']:
        stables_seen += 1
        if stables_seen >= 2:
            break
")

echo "Current version: $CURRENT"
echo ""

OPTIONS=()
LABELS=()

while IFS=' ' read -r TYPE TAG; do
    OPTIONS+=("$TAG")
    MARKER=""
    [[ "$TAG" == "$CURRENT" ]] && MARKER=", current"
    if [[ "$TYPE" == "stable" ]]; then
        LABELS+=("$TAG  (stable${MARKER})")
    else
        LABELS+=("$TAG  (release candidate${MARKER})")
    fi
done <<< "$VERSION_LIST"

if [[ ${#OPTIONS[@]} -eq 0 ]]; then
    echo "No releases found."
    exit 0
fi

echo "Available versions:"
for i in "${!OPTIONS[@]}"; do
    echo "  $((i+1))) ${LABELS[$i]}"
done
echo "  0) Cancel"
echo ""

while true; do
    read -rp "Select version to install [0-${#OPTIONS[@]}]: " CHOICE
    if [[ "$CHOICE" =~ ^[0-9]+$ ]] && [[ "$CHOICE" -ge 0 ]] && [[ "$CHOICE" -le "${#OPTIONS[@]}" ]]; then
        break
    fi
    echo "Invalid selection, please try again."
done

if [[ "$CHOICE" -eq 0 ]]; then
    echo "Cancelled."
    exit 0
fi

TARGET="${OPTIONS[$((CHOICE-1))]}"

if [[ "$TARGET" == "$CURRENT" ]]; then
    echo "$TARGET is already installed."
    exit 0
fi

echo "Installing $TARGET ..."

ASSET_INFO=$(echo "$ALL_RELEASES" | python3 -c "
import sys, json
target = '${TARGET}'
releases = json.load(sys.stdin)
r = next((r for r in releases if r['tag_name'] == target), None)
if r:
    bundle = next((a for a in r['assets'] if 'app-bundle' in a['name'] and a['name'].endswith('.tar.gz')), None)
    checksum = next((a for a in r['assets'] if 'app-bundle' in a['name'] and a['name'].endswith('.tar.gz.sha512')), None)
    if bundle and checksum:
        print(bundle['browser_download_url'])
        print(checksum['browser_download_url'])
")

BUNDLE_URL=$(echo "$ASSET_INFO" | sed -n '1p')
CHECKSUM_URL=$(echo "$ASSET_INFO" | sed -n '2p')

if [[ -z "$BUNDLE_URL" ]] || [[ -z "$CHECKSUM_URL" ]]; then
    echo "Error: missing app-bundle or checksum asset for $TARGET" >&2
    exit 1
fi

BUNDLE_FILENAME=$(basename "$BUNDLE_URL")
CHECKSUM_FILENAME=$(basename "$CHECKSUM_URL")

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

echo "Downloading $BUNDLE_URL ..."
curl -fL "$BUNDLE_URL" -o "$TMP/$BUNDLE_FILENAME"
echo "Downloading $CHECKSUM_URL ..."
curl -fL "$CHECKSUM_URL" -o "$TMP/$CHECKSUM_FILENAME"

(cd "$TMP" && sha512sum -c "$CHECKSUM_FILENAME")

systemctl stop "$SERVICE"

tar -xzf "$TMP/$BUNDLE_FILENAME" -C "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/pip" install --no-cache-dir -q -r "$INSTALL_DIR/requirements.txt"

# Self-update: replace this script with the version from the bundle
cp "$INSTALL_DIR/obs-update" /usr/local/bin/obs-update
chmod +x /usr/local/bin/obs-update
rm -f "$INSTALL_DIR/obs-update"

echo "$TARGET" > "$INSTALL_DIR/version"

systemctl start "$SERVICE"
echo "Done. Now running $TARGET."
SCRIPTEOF

sed -i "s|__REPO__|$REPO|g" /tmp/obs-update
chmod +x /tmp/obs-update
cp /tmp/obs-update obs-update

# ── App bundle ─────────────────────────────────────────────────────────────────
echo "==> Creating app bundle..."
tar -czf "/tmp/$APP_BUNDLE_FILE" obs/ gui_dist/ frontend_dist/ requirements.txt obs-update
(cd /tmp && sha512sum "$APP_BUNDLE_FILE" > "$APP_BUNDLE_FILE.sha512")
cp "/tmp/$APP_BUNDLE_FILE" "/tmp/$APP_BUNDLE_FILE.sha512" /output/

if [[ "${BUNDLE_ONLY}" == "true" ]]; then
    echo ""
    echo "==> Bundle-only mode complete. Artifacts:"
    ls -lh /output/
    exit 0
fi

# ── Base system (debootstrap or restore from cache) ───────────────────────────
mkdir -p "$ROOTFS"

if [[ "$NO_CACHE" != "true" ]] && [[ -f "$CACHE_FILE" ]]; then
    echo "==> Restoring base system from cache (~/.cache/obs-lxc-builder)..."
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

# ── Install app ────────────────────────────────────────────────────────────────
echo "==> Installing app into rootfs..."
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"

mount -t proc proc "$ROOTFS/proc"
trap 'mountpoint -q "$ROOTFS/proc" && umount "$ROOTFS/proc" || true' EXIT

mkdir -p "$ROOTFS/opt/obs"
cp -r obs              "$ROOTFS/opt/obs/"
cp -r gui_dist         "$ROOTFS/opt/obs/"
cp -r frontend_dist    "$ROOTFS/opt/obs/"
cp    requirements.txt "$ROOTFS/opt/obs/"
echo "$VERSION" | tee "$ROOTFS/opt/obs/version" > /dev/null
cp /tmp/obs-update "$ROOTFS/usr/local/bin/obs-update"

chroot "$ROOTFS" /bin/bash << 'INSTALL'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y python3 python3-pip python3-venv gcc libffi-dev libssl-dev mosquitto mosquitto-clients

python3 -m venv /opt/obs/venv
/opt/obs/venv/bin/pip install --no-cache-dir -r /opt/obs/requirements.txt

mkdir -p /data

cat > /etc/obs.env << 'EOF'
OBS_DATABASE__PATH=/data/obs.db
OBS_CONFIG=/data/config.yaml
EOF
chmod 600 /etc/obs.env

cat > /etc/systemd/system/obs.service << 'EOF'
[Unit]
Description=open bridge server
After=network.target mosquitto.service
Requires=mosquitto.service

[Service]
Type=simple
WorkingDirectory=/opt/obs
ExecStart=/opt/obs/venv/bin/python3 -m obs
Restart=on-failure
RestartSec=5
EnvironmentFile=/etc/obs.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/mosquitto/mosquitto.conf << 'MQTTCONF'
# No pid_file: systemd (Type=notify) manages the process lifecycle, and the
# Ubuntu mosquitto package ships no tmpfiles rule to create /run/mosquitto,
# so a pid_file there would fail on every boot (/run is a fresh tmpfs).

allow_anonymous false
password_file /etc/mosquitto/passwd

listener 1883
protocol mqtt

listener 9001
protocol websockets

persistence true
persistence_location /var/lib/mosquitto/

log_dest stdout
log_type error
log_type warning
log_type notice

max_connections 100
max_keepalive 120
MQTTCONF

cat > /opt/obs/obs-first-boot.sh << 'FIRSTBOOT'
#!/bin/bash
set -euo pipefail

MQTT_PASSWORD=$(python3 -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32)))")
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")

mosquitto_passwd -c -b /etc/mosquitto/passwd obs "$MQTT_PASSWORD"
chmod 640 /etc/mosquitto/passwd
chown mosquitto:mosquitto /etc/mosquitto/passwd

cat >> /etc/obs.env << EOF
OBS_MQTT__HOST=localhost
OBS_MQTT__PORT=1883
OBS_MQTT__USERNAME=obs
OBS_MQTT__PASSWORD=$MQTT_PASSWORD
OBS_MOSQUITTO__PASSWD_FILE=/etc/mosquitto/passwd
OBS_MOSQUITTO__RELOAD_COMMAND=systemctl reload mosquitto
OBS_MOSQUITTO__SERVICE_USERNAME=obs
OBS_MOSQUITTO__SERVICE_PASSWORD=$MQTT_PASSWORD
OBS_SECURITY__JWT_SECRET=$JWT_SECRET
EOF
chmod 600 /etc/obs.env
FIRSTBOOT
chmod +x /opt/obs/obs-first-boot.sh

cat > /etc/systemd/system/obs-first-boot.service << 'FIRSTBOOTSVC'
[Unit]
Description=open bridge server — first-boot MQTT credential setup
Before=mosquitto.service obs.service
ConditionPathExists=!/etc/obs-first-boot-done

[Service]
Type=oneshot
ExecStart=/opt/obs/obs-first-boot.sh
ExecStartPost=/bin/touch /etc/obs-first-boot-done
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
FIRSTBOOTSVC

systemctl enable obs.service mosquitto.service obs-first-boot.service

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
sha512sum "$TEMPLATE_FILE" > "$TEMPLATE_FILE.sha512"
cp "$TEMPLATE_FILE" "$TEMPLATE_FILE.sha512" /output/

echo ""
echo "==> Done! Artifacts:"
ls -lh /output/
