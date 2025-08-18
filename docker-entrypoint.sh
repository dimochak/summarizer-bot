#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] Backfilling today's messages for enabled chats..."
uv run python src/backfill_all_today.py || echo "[entrypoint] Backfill step finished with non-zero exit (continuing to start the bot)"

echo "[entrypoint] Starting bot..."
exec uv run python src/main.py