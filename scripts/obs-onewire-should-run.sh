#!/bin/sh
# SPDX-FileCopyrightText: 2026 abeggled and all contributors
# SPDX-License-Identifier: MIT
#
# obs-onewire-should-run.sh — startup gate for owserver.
#
# Used as ExecCondition= on owserver.service (LXC) and as the first check in
# the Docker sidecar entrypoint. Exits 0 only if the admin configured at
# least one 1-Wire bus master via OBS_ONEWIRE__* environment variables
# (populated by systemd's EnvironmentFile=/etc/obs.env or Docker Compose's
# environment:) — so owserver never runs as an idle, unconfigured service on
# installs that don't use 1-Wire.
set -eu

[ "${OBS_ONEWIRE__USB_ALL:-}" = "true" ] && exit 0
[ -n "${OBS_ONEWIRE__PBM_DEVICES:-}" ] && exit 0
exit 1
