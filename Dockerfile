# ---------------------------------------------------------------------------
# open bridge server — Multi-Stage Dockerfile (3 stages)
# Stage 1 (node-builder):   npm install + vite build → gui_dist/ + frontend_dist/
# Stage 2 (py-builder):     pip install Python deps
# Stage 3 (runtime):        python:3.11-slim, copies all artefacts
#
# Target: Linux x86_64 and ARM64 (Cortex-A72 / Raspberry Pi 4)
# ---------------------------------------------------------------------------

# ── Stage 1: build Vue Admin-GUI + Visu-Frontend ────────────────────────────
FROM node:24-slim AS node-builder
ARG VITE_INSTANCE_NAME=
ARG VITE_INSTANCE_COLOR=amber
ENV VITE_INSTANCE_NAME=${VITE_INSTANCE_NAME}
ENV VITE_INSTANCE_COLOR=${VITE_INSTANCE_COLOR}

# Admin-GUI (gui/ → ../gui_dist)
WORKDIR /gui-src
COPY gui/package.json ./
RUN npm install --prefer-offline
COPY gui/ ./
RUN npm run build
# Output: /gui_dist

# Visu-Frontend (frontend/ → ../frontend_dist)
WORKDIR /visu-src
COPY frontend/package.json ./
RUN npm install --prefer-offline
COPY frontend/ ./
RUN npm run build
# Output: /frontend_dist


# ── Stage 2: Python dependency builder ─────────────────────────────────────
FROM python:3.14-slim AS py-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 3: runtime image ──────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

LABEL org.opencontainers.image.title="open bridge server" \
      org.opencontainers.image.description="Open-Source Multiprotocol Server for Building Automation" \
      org.opencontainers.image.licenses="MIT"

# Python packages from builder
COPY --from=py-builder /install /usr/local

# Application source
WORKDIR /app
COPY obs/ ./obs/

# Built Admin-GUI (served by FastAPI from /app/gui_dist)
COPY --from=node-builder /gui_dist ./gui_dist/

# Built Visu SPA (served by FastAPI from /app/frontend_dist under /visu/)
COPY --from=node-builder /frontend_dist ./frontend_dist/

# Pre-create data directory — volume mount inherits this, preventing SQLite errors
RUN mkdir -p /data

# Data volume — DB files, ringbuffer disk, optional config.yaml
VOLUME ["/data"]

# Runtime defaults — overridable via env or mounted /data/config.yaml
ENV OBS_DATABASE__PATH=/data/obs.db \
    OBS_CONFIG=/data/config.yaml

EXPOSE 8080

CMD ["python", "-m", "obs"]
