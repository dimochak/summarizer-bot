from datetime import datetime, timedelta
from html import escape
from telegram import Chat

import src.config as config

def utc_ts(dt: datetime) -> int:
    return int(dt.timestamp())

def local_midnight_bounds(day_local: datetime):
    day = day_local.astimezone(config.KYIV).date()
    start_local = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=config.KYIV)
    end_local   = datetime.combine(day,               datetime.min.time(), tzinfo=config.KYIV)
    return start_local, end_local

def message_link(chat: Chat, message_id: int) -> str:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    cid = str(chat.id)
    if cid.startswith("-100"):
        cid = cid[4:]
    else:
        cid = cid.lstrip("-")
    return f"https://t.me/c/{cid}/{message_id}"

def user_link(user_id: int, username: str | None, full_name: str) -> str:
    label = escape(full_name or (username and f"@{username}") or "Користувач")
    if username:
        return f'<a href="https://t.me/{escape(username)}">{label}</a>'
    return f'<a href="tg://user?id={user_id}">{label}</a>'

def clean_text(s: str | None) -> str:
    if not s:
        return ""
    return s.strip()
