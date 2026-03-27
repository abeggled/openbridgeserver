# ---------------------------------------------------------------------------
# OpenTWS — Multi-Stage Dockerfile (3 stages)
# Stage 1 (node-builder):   npm ci + vite build → gui_dist/
# Stage 2 (py-builder):     pip install Python deps
# Stage 3 (runtime):        python:3.11-slim, copies both artefacts
#
# Target: Linux x86_64 and ARM64 (Cortex-A72 / Raspberry Pi 4)
# ---------------------------------------------------------------------------

# ── Stage 1: build Vue GUI ──────────────────────────────────────────────────
FROM node:20-slim AS node-builder

WORKDIR /gui-src
COPY gui/package.json gui/package-lock.json* ./
RUN npm ci --prefer-offline

COPY gui/ ./
RUN npm run build
# Output: /gui-src/../gui_dist  (vite outDir is ../gui_dist)


# ── Stage 2: Python dependency builder ─────────────────────────────────────
FROM python:3.11-slim AS py-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 3: runtime image ──────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="OpenTWS" \
      org.opencontainers.image.description="Open-Source Multiprotocol Server for Building Automation" \
      org.opencontainers.image.licenses="MIT"

# Python packages from builder
COPY --from=py-builder /install /usr/local

# Application source
WORKDIR /app
COPY opentws/ ./opentws/

# Built GUI (served as static files by FastAPI from /app/gui_dist)
COPY --from=node-builder /gui_dist ./gui_dist/

# Pre-create data directory — volume mount inherits this, preventing SQLite errors
RUN mkdir -p /data

# Data volume — DB files, ringbuffer disk, optional config.yaml
VOLUME ["/data"]

# Runtime defaults — overridable via env or mounted /data/config.yaml
ENV OPENTWS_DATABASE__PATH=/data/opentws.db \
    OPENTWS_CONFIG=/data/config.yaml

EXPOSE 8080

CMD ["python", "-m", "opentws"]
