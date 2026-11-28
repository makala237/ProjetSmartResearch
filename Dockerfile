# syntax=docker/dockerfile:1

# =========================================================================
# Stage 1: Build Frontend SPA (React + TypeScript + Vite)
# =========================================================================
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# =========================================================================
# Stage 2: Production Runtime (FastAPI + LibreOffice Headless + Poppler + Fonts)
# =========================================================================
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    RENDER_ENGINE=libreoffice \
    PORT=8080

WORKDIR /app

# Install LibreOffice Writer/Impress, poppler-utils (pdftoppm), metric-compatible Office fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    libreoffice-impress \
    libreoffice-common \
    fonts-liberation \
    fonts-liberation2 \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-dejavu-core \
    fontconfig \
    poppler-utils \
    curl \
    ca-certificates \
  && fc-cache -f -v \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Install Python runtime dependencies + Cloud Storage SDKs
COPY backend/requirements.txt backend/requirements-cloud.txt /app/backend/
RUN pip install --no-cache-dir -r /app/backend/requirements.txt -r /app/backend/requirements-cloud.txt

# Copy backend source, master docx canvas, presentation templates, pedagogy notes, and built frontend assets
COPY backend /app/backend
COPY 2025_Canevas_protocole.docx /app/2025_Canevas_protocole.docx
COPY tamplates /app/tamplates
COPY pedagogie /app/pedagogie
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /app/docker/entrypoint.sh

RUN sed -i 's/\r$//' /app/docker/entrypoint.sh \
  && chmod +x /app/docker/entrypoint.sh \
  && mkdir -p /app/backend/storage/exports /app/backend/storage/uploads /app/backend/storage/working_memory /app/backend/templates \
  && groupadd --gid 1001 smartuser \
  && useradd --uid 1001 --gid 1001 --create-home --home-dir /home/smartuser smartuser \
  && chown -R smartuser:smartuser /app /home/smartuser

ENV HOME=/home/smartuser
USER smartuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/api/health" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
