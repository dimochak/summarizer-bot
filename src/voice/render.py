"""Шаблони для голосових: промпт для Gemini і токсичні відповіді.

Фрази живуть у самих .j2 і обираються фільтром `random`, тож нову фразу
додають правкою одного файлу, без коду.

Автоекранування увімкнене: ім'я та розшифровка — це текст користувача, а
відповідь іде в Telegram як HTML.
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).parent / "templates"


def plural_uk(n: int, one: str, few: str, many: str) -> str:
    """Форма слова після числа: 1 секунда, 2 секунди, 5 секунд, 11 секунд."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def uk_duration(seconds: int) -> str:
    """Тривалість словами: «42 секунди», «2 хвилини 5 секунд»."""
    seconds = max(int(seconds or 0), 0)
    minutes, rest = divmod(seconds, 60)
    parts = []
    if minutes:
        parts.append(f"{minutes} {plural_uk(minutes, 'хвилина', 'хвилини', 'хвилин')}")
    if rest or not minutes:
        parts.append(f"{rest} {plural_uk(rest, 'секунда', 'секунди', 'секунд')}")
    return " ".join(parts)


_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
    # Забута змінна в шаблоні має падати в тестах, а не тихо давати порожнечу.
    undefined=StrictUndefined,
)
_env.filters["uk_duration"] = uk_duration


def render(name: str, **context) -> str:
    return _env.get_template(name).render(**context).strip()
