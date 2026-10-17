"""Блок «топ за реакціями» для щоденного дайджесту."""
from collections import Counter
from html import escape

from src.tools.db import db_call, get_top_posts
from src.tools.utils import message_link

TOP_POSTS_LIMIT = 5
# Коротко, щоб блок разом із підсумком влазив в одне повідомлення.
PREVIEW_CHARS = 50
MAX_EMOJIS = 3

KIND_LABELS = {
    "photo": "📷 фото",
    "animation": "🎞 гіфка",
    "video": "🎬 відео",
    "video_note": "📹 кружечок",
    "voice": "🎤 голосове",
    "audio": "🎵 аудіо",
    "sticker": "стікер",
    "document": "📎 файл",
    "poll": "📊 опитування",
}


def _label(row) -> str:
    preview = " ".join((row.get("preview") or "").split())
    if len(preview) > PREVIEW_CHARS:
        preview = preview[:PREVIEW_CHARS].rstrip() + "…"
    kind = KIND_LABELS.get(row["kind"])
    if kind and preview:
        return f"{kind}: {preview}"
    return kind or preview or "повідомлення"


def _emojis(reactions) -> str:
    # Кастомні й платні реакції в тексті бота не відобразити — показуємо значком.
    shown = []
    for reaction, _ in Counter(reactions or []).most_common():
        if reaction.startswith("custom:"):
            reaction = "✨"
        elif reaction == "paid":
            reaction = "⭐"
        if reaction not in shown:
            shown.append(reaction)
        if len(shown) == MAX_EMOJIS:
            break
    return "".join(shown)


def format_top_posts(chat, rows) -> str:
    if not rows:
        return ""
    lines = [f"<b>🔥 Топ-{len(rows)} за реакціями</b>"]
    for i, row in enumerate(rows, 1):
        link = message_link(chat, row["message_id"])
        author = row.get("full_name") or (row.get("username") and f"@{row['username']}") or "Анонім"
        lines.append(
            f'{i}. <a href="{link}">{escape(_label(row))}</a> — {escape(author)} · '
            f'{row["reactors"]} {_emojis(row.get("reactions"))}'.rstrip()
        )
    return "\n".join(lines)


async def top_posts_block(chat, start_ts_utc: int, end_ts_utc: int) -> str:
    rows = await db_call(get_top_posts, chat.id, start_ts_utc, end_ts_utc, TOP_POSTS_LIMIT)
    return format_top_posts(chat, rows)
