#!/usr/bin/env bash
# render_start.sh — Render web service start script for the backend
# Runs on every deploy: seeds DB (idempotent) then starts uvicorn.
set -e

echo "=== DhakaPath Backend — Render Startup ==="

# 1. Create tables & seed demo users + fare rules (normalized source = real datasets)
#    Falls back gracefully if normalized JSON files are missing.
echo "[startup] Normalizing datasets (generated files are not in git)..."
python -m app.scripts.normalize_datasets > /dev/null

echo "[startup] Running database seed..."
python -m app.scripts.seed --source normalized 2>/dev/null || \
python -m app.scripts.seed --source fixtures
echo "[startup] Seed complete."

# 2. Start the production server
echo "[startup] Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
