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

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Application source
WORKDIR /app
COPY opentws/ ./opentws/

# Pre-create data directory — prevents "unable to open database file" when
# the Docker named volume is first mounted (volume inherits this directory).
RUN mkdir -p /data

# Data volume — DB files, ringbuffer disk, optional config.yaml
VOLUME ["/data"]

# Runtime defaults — overridable via env or mounted /data/config.yaml
ENV OPENTWS_DATABASE__PATH=/data/opentws.db \
    OPENTWS_CONFIG=/data/config.yaml

EXPOSE 8080

CMD ["python", "-m", "opentws"]
