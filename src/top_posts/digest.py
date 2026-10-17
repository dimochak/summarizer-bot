"""Блок «топ за реакціями» для щоденного дайджесту."""
from html import escape

from src.tools.db import db_call, get_top_posts
from src.tools.utils import message_link

TOP_POSTS_LIMIT = 5


def format_top_posts(chat, rows) -> str:
    if not rows:
        return ""
    lines = [f"<b>🔥 Топ-{len(rows)} за реакціями</b>"]
    for i, row in enumerate(rows, 1):
        link = message_link(chat, row["message_id"])
        author = row.get("full_name") or (row.get("username") and f"@{row['username']}") or "Анонім"
        lines.append(f'{i}. <a href="{link}">посилання</a> — 👍 {row["reactors"]} — {escape(author)}')
    return "\n".join(lines)


async def top_posts_block(chat, start_ts_utc: int, end_ts_utc: int) -> str:
    rows = await db_call(get_top_posts, chat.id, start_ts_utc, end_ts_utc, TOP_POSTS_LIMIT)
    return format_top_posts(chat, rows)
