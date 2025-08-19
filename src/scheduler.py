from datetime import datetime, time as dtime
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, Application

import src.config as config
from src.db import get_enabled_chat_ids
from src.gemini import summarize_day
from src.utils import local_midnight_bounds

async def send_daily_summary_to_chat(app: Application, chat_id: int):
    try:
        chat = await app.bot.get_chat(chat_id)
    except Exception as e:
        config.log.exception("Cannot get chat %s: %s", chat_id, e)
        return
    now_local = datetime.now(tz=config.KYIV)
    start_local, end_local = local_midnight_bounds(now_local)
    text = await summarize_day(chat, start_local, end_local, None)
    if not text:
        text = f"<b>#Підсумки_дня — {start_local.date():%d.%m.%Y}</b>\n\nНемає повідомлень або не вдалося сформувати підсумок."
    await app.bot.send_message(chat_id=chat.id, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def send_all_summaries_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    chat_ids = get_enabled_chat_ids()
    if config.ALLOWED_CHAT_IDS:
        chat_ids = [cid for cid in chat_ids if cid in config.ALLOWED_CHAT_IDS]
    if not chat_ids:
        config.log.info("No enabled chats to summarize.")
        return
    for cid in chat_ids:
        await send_daily_summary_to_chat(app, cid)
    config.log.info("Daily summaries sent to %d chats", len(chat_ids))

def schedule_daily(app: Application):
    app.job_queue.run_daily(
        send_all_summaries_job,
        time=dtime(22, 15, tzinfo=config.KYIV),
        name="daily_summary_all"
    )
    config.log.info("Daily job scheduled for 00:00 %s", config.TZ)