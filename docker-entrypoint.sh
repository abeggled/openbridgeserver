#!/bin/sh
# open bridge server container entrypoint — per-instance first-boot secrets.
#
# Docker counterpart to obs-first-boot.service in the Proxmox LXC template
# (tools/_lxc-inner.sh): generate a random JWT secret on the very first start
# and persist it, so no installation keeps running on the documented
# placeholder. Where the LXC template writes /etc/obs.env, the container uses
# the /data volume.
set -eu

DATA_DIR="${OBS_DATA_DIR:-/data}"
SECRETS_DIR="$DATA_DIR/secrets"
JWT_SECRET_FILE="$SECRETS_DIR/jwt-secret"
CONFIG_FILE="${OBS_CONFIG:-$DATA_DIR/config.yaml}"

# A secret set in a mounted config.yaml is an operator decision too. If PyYAML
# is missing or the file cannot be read, the secret counts as unset — better to
# generate one than to silently keep running on the placeholder.
config_defines_jwt_secret() {
    [ -f "$CONFIG_FILE" ] || return 1
    python3 -c '
import sys

try:
    import yaml

    with open(sys.argv[1], encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    secret = (data.get("security") or {}).get("jwt_secret") or ""
except Exception:
    secret = ""
placeholders = {"changeme", "change-this-to-a-random-secret-min-32-chars"}
sys.exit(0 if secret and secret not in placeholders else 1)
' "$CONFIG_FILE"
}

# Values that are not an operator decision: unset, empty, or one of the
# placeholders from docker-compose.yml / .env.example. Any other value wins —
# the entrypoint never touches a secret the operator chose.
case "${OBS_SECURITY__JWT_SECRET:-}" in
    "" | changeme | change-this-to-a-random-secret-min-32-chars)
        # Clear it first: the env var takes precedence over config.yaml, so an
        # empty placeholder would otherwise override a secret set there.
        unset OBS_SECURITY__JWT_SECRET
        if ! config_defines_jwt_secret; then
            if [ ! -s "$JWT_SECRET_FILE" ]; then
                mkdir -p "$SECRETS_DIR"
                chmod 700 "$SECRETS_DIR"
                # umask instead of a later chmod: the secret is never briefly readable.
                (umask 077 && python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$JWT_SECRET_FILE")
                # To stderr: stdout belongs to the exec'ed command (e.g.
                # `docker compose run obs obs-admin --json ...`).
                echo "open bridge server: generated a per-instance JWT secret in $JWT_SECRET_FILE (first start)." >&2
            fi
            OBS_SECURITY__JWT_SECRET="$(cat "$JWT_SECRET_FILE")"
            export OBS_SECURITY__JWT_SECRET
        fi
        ;;
esac

exec "$@"
