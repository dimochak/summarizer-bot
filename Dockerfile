FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

RUN apt update && \
    apt install -y sqlite3 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev && \
    uv add "telethon==1.*"

COPY . .

COPY docker-entrypoint.sh /app/docker-entrypoint.sh

CMD ["./docker-entrypoint.sh"]

