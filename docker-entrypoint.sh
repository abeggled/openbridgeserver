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

# Any secret the operator supplied elsewhere counts as their decision, and the
# lookup mirrors what obs/config.py does with the same environment:
#
#   * the legacy OPENTWS_SECURITY__JWT_SECRET variable, which _import_legacy_env_vars()
#     maps onto OBS_* only while OBS_* is unset — hence the caller's unset below,
#   * a case variant of OBS_SECURITY__JWT_SECRET, which pydantic-settings reads
#     case-insensitively,
#   * a secret in the mounted config.yaml, whose path follows _config_path():
#     OBS_CONFIG, or OPENTWS_CONFIG when OBS_CONFIG is unset or still holds the
#     image's own /data/config.yaml default (the docker_defaults rule).
#
# If PyYAML is missing or the file cannot be read, the config secret counts as
# unset — better to generate one than to keep running on the placeholder.
operator_defines_jwt_secret() {
    python3 -c '
import os
import sys

PLACEHOLDERS = {"", "changeme", "change-this-to-a-random-secret-min-32-chars"}
IMAGE_DEFAULT_CONFIG = "/data/config.yaml"


def env_value(name):
    wanted = name.upper()
    for key, value in os.environ.items():
        if key.upper() == wanted:
            return value
    return ""


def config_path():
    path = env_value("OBS_CONFIG")
    legacy = env_value("OPENTWS_CONFIG")
    if legacy and path in ("", IMAGE_DEFAULT_CONFIG):
        return legacy
    return path or sys.argv[1]


secret = env_value("OBS_SECURITY__JWT_SECRET") or env_value("OPENTWS_SECURITY__JWT_SECRET")
if secret in PLACEHOLDERS:
    try:
        import yaml

        with open(config_path(), encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        secret = (data.get("security") or {}).get("jwt_secret") or ""
    except Exception:
        secret = ""
sys.exit(1 if secret in PLACEHOLDERS else 0)
' "$DATA_DIR/config.yaml"
}

# Values that are not an operator decision: unset, empty, or one of the
# placeholders from docker-compose.yml / .env.example. Any other value wins —
# the entrypoint never touches a secret the operator chose.
case "${OBS_SECURITY__JWT_SECRET:-}" in
    "" | changeme | change-this-to-a-random-secret-min-32-chars)
        # Clear it first: the env var takes precedence over config.yaml and over
        # the legacy variable, so an empty placeholder would otherwise override
        # a secret set there.
        unset OBS_SECURITY__JWT_SECRET
        if ! operator_defines_jwt_secret; then
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
