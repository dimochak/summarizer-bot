FROM ghcr.io/astral-sh/uv:python3.11-bookworm
WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
RUN uv add "telethon==1.*"

COPY . .

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Entrypoint
CMD ["./docker-entrypoint.sh"]

