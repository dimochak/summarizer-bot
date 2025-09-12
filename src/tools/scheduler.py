from datetime import datetime, time as dtime
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, Application

import src.tools.config as config
from src.tools.db import get_enabled_chat_ids
from src.summarizer.summarizer import summarize_day
from src.tools.utils import local_midnight_bounds


async def send_daily_summary_to_chat(app: Application,
                                     chat_id: int,
                                     start_local: datetime,
                                     end_local: datetime):
    try:
        chat = await app.bot.get_chat(chat_id)
    except Exception as e:
        config.log.exception("Cannot get chat %s: %s", chat_id, e)
        return
    text = await summarize_day(chat, start_local, end_local, None, toxicity_level=9)
    if not text:
        text = f"<b>#Підсумки_дня — {start_local.date():%d.%m.%Y}</b>\n\nНемає повідомлень або не вдалося сформувати підсумок."
    await app.bot.send_message(
        chat_id=chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def send_all_summaries_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    chat_ids = get_enabled_chat_ids()

    # Filter chat_ids to only include those that are configured for AI providers
    configured_chat_ids = [cid for cid in chat_ids if cid in config.ALLOWED_CHAT_IDS]

    if not configured_chat_ids:
        config.log.info("No enabled and configured chats to summarize.")
        return

    now_local = datetime.now(tz=config.KYIV)
    start_local, end_local = local_midnight_bounds(now_local)
    for cid in configured_chat_ids:
        await send_daily_summary_to_chat(app, cid, start_local, end_local)

    config.log.info(f"Daily summaries sent to {len(configured_chat_ids)} chats")


def schedule_daily(app: Application):
    hour = 23
    minute = 59
    app.job_queue.run_daily(
        send_all_summaries_job,
        time=dtime(hour, minute,
                   tzinfo=config.KYIV),
        name="daily_summary_all",
    )
    config.log.info(f"Daily job scheduled for {hour}:{minute}, {config.TZ}")
