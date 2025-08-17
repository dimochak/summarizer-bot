FROM ghcr.io/astral-sh/uv:python3.11-bookworm
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Кладемо код
COPY . .

# Запуск
CMD ["uv", "run", "python", "main.py"]
