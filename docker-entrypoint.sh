#!/bin/sh
# OpenTWS entrypoint — fixes /data ownership, then drops to opentws user
# Runs as root initially so it can chown the volume mount point.
set -e

# Ensure the data directory exists and is writable by the opentws user.
# This is needed because Docker named volumes are created as root:root by default.
mkdir -p /data
chown opentws:opentws /data

# Drop privileges and exec the main process
exec su-exec opentws "$@"
