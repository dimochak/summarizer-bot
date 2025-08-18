#!/usr/bin/env bash
set -euo pipefail

# docker-entrypoint.sh
# 1) Optional: backfill today's messages for all enabled chats (if Telethon API creds are present)
# 2) Start the bot

# Expect TELEGRAM_API_ID and TELEGRAM_API_HASH to be provided as secrets/env for backfill
if [[ -n "${TELEGRAM_API_ID:-}" && -n "${TELEGRAM_API_HASH:-}" ]]; then
  echo "[entrypoint] Backfilling today's messages for enabled chats..."
  uv run python src/backfill_all_today.py || echo "[entrypoint] Backfill step finished with non-zero exit (continuing to start the bot)"
else
  echo "[entrypoint] TELEGRAM_API_ID/TELEGRAM_API_HASH not set — skipping backfill."
fi

echo "[entrypoint] Starting bot..."
exec uv run python src/main.py