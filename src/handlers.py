import asyncio
from datetime import datetime, timezone, time as dtime
from contextlib import closing
import random

from telegram import Update, Chat, Message
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, JobQueue

import src.config as config
from src.db import db, ensure_chat_record, add_message
from src.gemini import summarize_day
from src.utils import utc_ts

INITIAL_PLACEHOLDERS = [
    "⏳ Окей, я подивлюся, що ви там набазікали. Тільки не очікуйте нічого геніального.",
    "🧐 Викликали? Навіщо? Ну добре, зараз спробую знайти хоч одну розумну думку у вашому чаті.",
    "🤖 Запускаю аналіз вашого словесного потоку. Не заздрю собі.",
    "⏳ Зараз, зараз, дай переварити все це сміття, що ви називаєте розмовою.",
]

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.effective_message
    chat: Chat = update.effective_chat

    if config.ALLOWED_CHAT_IDS and chat.id not in config.ALLOWED_CHAT_IDS:
        return

    ensure_chat_record(chat, enable_default=0)

    text = msg.text or msg.caption
    if text is None:
        return

    ts = msg.date
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    add_message(
        chat.id,
        msg.message_id,
        (msg.from_user and msg.from_user.id) or None,
        (msg.from_user and msg.from_user.username) or None,
        (msg.from_user and msg.from_user.full_name) or None,
        text,
        (msg.reply_to_message and msg.reply_to_message.message_id) or None,
        utc_ts(ts.astimezone(timezone.utc))
    )

async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(f"<code>{update.effective_chat.id}</code>")

async def cmd_summary_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if config.ALLOWED_CHAT_IDS and chat.id not in config.ALLOWED_CHAT_IDS:
        return

    # Send a placeholder message first to acknowledge the command
    placeholder_message = await update.effective_message.reply_html(random.choice(INITIAL_PLACEHOLDERS))

    # Perform the long-running summary generation
    now_local = datetime.now(tz=config.KYIV)
    start_local = datetime.combine(now_local.date(), dtime.min, tzinfo=config.KYIV)  # сьогодні від 00:00
    text = await summarize_day(chat, start_local, now_local, context)

    # Prepare the final text
    if not text:
        text = "<b>#Підсумки_дня — сьогодні</b>\n\nПоки що немає даних або нічого не згрупувалося."

    # Edit the placeholder message with the final summary
    await placeholder_message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


async def cmd_enable_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    ensure_chat_record(chat, enable_default=1)
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("UPDATE chats SET enabled=1 WHERE chat_id=?", (chat.id,))
        conn.commit()
    await update.effective_message.reply_text("✅ Daily summaries enabled for this chat.")

async def cmd_disable_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    ensure_chat_record(chat, enable_default=0)
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("UPDATE chats SET enabled=0 WHERE chat_id=?", (chat.id,))
        conn.commit()
    await update.effective_message.reply_text("🚫 Daily summaries disabled for this chat.")

async def cmd_status_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT enabled FROM chats WHERE chat_id=?", (chat.id,))
        row = cur.fetchone()
    enabled = (row and row["enabled"] == 1)
    await update.effective_message.reply_text(
        f"Status: {'ENABLED ✅' if enabled else 'DISABLED 🚫'} for this chat."
    )