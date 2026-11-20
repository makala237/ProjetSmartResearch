#!/bin/sh
set -eu

DB_BACKEND_VAL="${DB_BACKEND:-sql}"
if [ "${DB_BACKEND_VAL}" != "firestore" ] && [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  echo "[Smart Research] Exécution des migrations Alembic..."
  alembic -c /app/backend/alembic.ini upgrade head
else
  echo "[Smart Research] Migrations Alembic ignorées (db_backend=${DB_BACKEND_VAL}, run_migrations=${RUN_MIGRATIONS_ON_START:-true})."
fi

PORT_TO_USE="${PORT:-8080}"
WORKERS_TO_USE="${UVICORN_WORKERS:-1}"

echo "[Smart Research] Démarrage serveur ASGI sur 0.0.0.0:${PORT_TO_USE} (workers=${WORKERS_TO_USE}, db=${DB_BACKEND_VAL}, engine=${RENDER_ENGINE:-auto}, storage=${STORAGE_BACKEND:-local})..."
exec uvicorn app.main:app \
  --app-dir /app/backend \
  --host 0.0.0.0 \
  --port "${PORT_TO_USE}" \
  --workers "${WORKERS_TO_USE}" \
  --timeout-keep-alive 75
