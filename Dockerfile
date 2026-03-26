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

# Non-root user for security
RUN addgroup --system opentws && adduser --system --ingroup opentws opentws

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Application source
WORKDIR /app
COPY opentws/ ./opentws/

# Data volume — DB files, ringbuffer disk, config.yaml
VOLUME ["/data"]

# Runtime config via env or mounted config.yaml
ENV OPENTWS_DATABASE__PATH=/data/opentws.db \
    OPENTWS_CONFIG=/data/config.yaml

# Switch to non-root
USER opentws

EXPOSE 8080

ENTRYPOINT ["python", "-m", "opentws"]
