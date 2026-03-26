# ---------------------------------------------------------------------------
# OpenTWS — Multi-Stage Dockerfile
# Target: Linux x86_64 and ARM64 (Cortex-A72)
# Base:   python:3.11-slim  (Debian Bookworm slim)
# ---------------------------------------------------------------------------

# ── Stage 1: dependency builder ────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps required by some Python packages (pymodbus serial, cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="OpenTWS" \
      org.opencontainers.image.description="Open-Source Multiprotocol Server for Building Automation" \
      org.opencontainers.image.licenses="MIT"

# su-exec: lightweight gosu alternative for privilege dropping (Debian package)
RUN apt-get update && apt-get install -y --no-install-recommends \
        su-exec \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — created before VOLUME so ownership can be pre-set
RUN addgroup --system opentws && adduser --system --ingroup opentws opentws

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Application source
WORKDIR /app
COPY opentws/ ./opentws/

# Entrypoint script — runs as root, fixes /data permissions, then drops to opentws
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Pre-create /data with correct ownership.
# When a named volume is first mounted, Docker copies the image directory,
# preserving this ownership. On subsequent mounts the volume is used as-is,
# but the entrypoint re-chowns on every start to handle pre-existing volumes.
RUN mkdir -p /data && chown opentws:opentws /data

# Data volume — DB files, ringbuffer disk, optional config.yaml
VOLUME ["/data"]

# Runtime defaults — overridable via env or mounted /data/config.yaml
ENV OPENTWS_DATABASE__PATH=/data/opentws.db \
    OPENTWS_CONFIG=/data/config.yaml

EXPOSE 8080

# Entrypoint runs as root to fix /data ownership, then su-exec drops to opentws
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "opentws"]
