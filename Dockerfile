FROM ghcr.io/astral-sh/uv:python3.11-bookworm
WORKDIR /app

# Dependencies (locked via uv.lock if present)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
# Add Telethon for backfill script
RUN uv add "telethon==1.*"

# App code
COPY . .

# Add entrypoint that backfills today, then starts the bot
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Entrypoint
CMD ["./docker-entrypoint.sh"]

