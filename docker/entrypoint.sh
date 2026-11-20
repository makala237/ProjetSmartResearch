#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  echo "[Smart Research] Exécution des migrations Alembic..."
  alembic -c /app/backend/alembic.ini upgrade head
fi

PORT_TO_USE="${PORT:-8080}"
WORKERS_TO_USE="${UVICORN_WORKERS:-1}"

echo "[Smart Research] Démarrage serveur ASGI sur 0.0.0.0:${PORT_TO_USE} (workers=${WORKERS_TO_USE}, engine=${RENDER_ENGINE:-auto}, storage=${STORAGE_BACKEND:-local})..."
exec uvicorn app.main:app \
  --app-dir /app/backend \
  --host 0.0.0.0 \
  --port "${PORT_TO_USE}" \
  --workers "${WORKERS_TO_USE}" \
  --timeout-keep-alive 75
