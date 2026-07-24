# ---------------------------------------------------------------------------
# open bridge server — owserver sidecar (1-Wire bus server, issue #1040)
#
# Bridges USB/serial 1-Wire bus masters (plain USB sticks, ElabNET PBM) into a
# TCP service that the ONEWIRE adapter connects to. Debian-based rather than
# Alpine: Debian/Ubuntu's `owserver` package is built against libusb and
# supports plain USB busmasters (`server: usb = all`); Alpine's own `owfs`
# package is compiled *without* USB support (no libusb build dependency) and
# only works for the PBM/serial case — confirmed by actually building and
# running both. Trading a larger image for a package that isn't missing a
# feature we need.
#
# Build context must be the repo root (the entrypoint reuses the same
# scripts/ files as the LXC template, single source of truth):
#   docker build -f tools/docker/owserver.Dockerfile .
# ---------------------------------------------------------------------------
FROM debian:trixie-slim

LABEL org.opencontainers.image.title="open bridge server — owserver sidecar" \
      org.opencontainers.image.description="1-Wire bus server (OWFS/owserver) for the ONEWIRE adapter" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update && apt-get install -y --no-install-recommends \
        owserver \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/obs-onewire-configure.sh scripts/obs-onewire-should-run.sh /usr/local/bin/
COPY tools/docker/owserver-entrypoint.sh /usr/local/bin/owserver-entrypoint.sh
RUN chmod +x /usr/local/bin/obs-onewire-configure.sh \
             /usr/local/bin/obs-onewire-should-run.sh \
             /usr/local/bin/owserver-entrypoint.sh

EXPOSE 4304

ENTRYPOINT ["/usr/local/bin/owserver-entrypoint.sh"]
