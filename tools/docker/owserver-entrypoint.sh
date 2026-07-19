#!/bin/sh
# SPDX-FileCopyrightText: 2026 abeggled and all contributors
# SPDX-License-Identifier: MIT
#
# Entrypoint for the owserver sidecar image (tools/docker/owserver.Dockerfile).
# Mirrors the LXC template's systemd ExecCondition/ExecStartPre gate: exits
# cleanly without starting owserver if no 1-Wire bus master is configured, so
# the container doesn't sit there as an unconfigured, idle daemon.
set -eu

if ! /usr/local/bin/obs-onewire-should-run.sh; then
    echo "1-Wire not configured (set OBS_ONEWIRE__USB_ALL=true and/or OBS_ONEWIRE__PBM_DEVICES) — owserver will not start." >&2
    exit 0
fi

/usr/local/bin/obs-onewire-configure.sh
exec owserver --foreground -c /etc/owfs.conf
