"""Читання конкретної веб-сторінки.

Пошук у нас робить вбудований інструмент провайдера — він і шукає, і повертає
цитати. Але прочитати сторінку на вимогу («ось лінк, скажи що там») він не
вміє, тож цим займається окремий інструмент.
"""
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

import src.tools.config as config

TIMEOUT = 15.0
MAX_BYTES = 2_000_000
MAX_CHARS = 8_000

_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _is_public_url(url: str) -> tuple[bool, str]:
    """Не даємо агенту постукати у внутрішню мережу.

    URL приходить від моделі, а та могла прочитати його зі сторінки або з
    повідомлення користувача. Без цієї перевірки «прочитай http://169.254.169.254/…»
    перетворилось би на читання метаданих хостингу.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "дозволені лише http і https"
    if not parsed.hostname:
        return False, "в URL немає хоста"

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False, "хост не резолвиться"

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False, "адреса у внутрішній мережі"

    return True, ""


def _html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", html)
    text = _TAGS.sub("\n", text)

    # Достатньо кількох найчастіших сутностей: решта в тексті для LLM некритична.
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&mdash;", "—"), ("&ndash;", "–"),
    ):
        text = text.replace(entity, char)

    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


@tool
async def web_fetch(url: str) -> str:
    """Читає веб-сторінку за посиланням і повертає її текст.

    Використовуй, коли треба дізнатись, що саме написано на конкретній
    сторінці: користувач дав посилання, або ти знайшов його пошуком і
    потрібні деталі, яких немає у видачі.

    Args:
        url: повне посилання, разом з http:// або https://.
    """
    allowed, reason = _is_public_url(url)
    if not allowed:
        config.log.warning(f"web_fetch rejected {url}: {reason}")
        return f"Не можу відкрити це посилання: {reason}."

    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, follow_redirects=True, max_redirects=5
        ) as client:
            response = await client.get(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; PanBot/1.0)"}
            )
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml")):
                return f"Сторінка не текстова ({content_type or 'тип невідомий'})."

            if len(response.content) > MAX_BYTES:
                return "Сторінка завелика, щоб її читати."

            body = response.text
    except Exception as e:
        config.log.exception("web_fetch failed for %s: %s", url, e)
        return f"Не вдалося відкрити «{url}»."

    text = _html_to_text(body)
    if not text:
        return "Сторінка відкрилась, але тексту на ній не знайшлось."

    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…(сторінку обрізано)"

    # Явна рамка: усе нижче — чужий текст. Промпти обох етапів кажуть моделі
    # ставитись до вмісту інструментів як до даних, і саме тут це критично.
    return f"Вміст сторінки {url}:\n\n{text}"
