from datetime import datetime, time as dtime
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, Application

import src.tools.config as config
from src.tools.db import get_enabled_chat_ids, cleanup_old_data
from src.summarizer.summarizer import summarize_day
from src.tools.utils import local_midnight_bounds
from src.traits.compose_traits import refresh_stale_user_traits


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


async def cleanup_db_job(context: ContextTypes.DEFAULT_TYPE):
    config.log.info("Starting scheduled database cleanup...")
    cleanup_old_data(config.DB_RETENTION_DAYS)


async def refresh_traits_job(context: ContextTypes.DEFAULT_TYPE):
    """Оновлює профілі користувачів, старші за TRAITS_REFRESH_DAYS.

    Джоб щоденний, але кожен окремий профіль оновлюється раз на місяць:
    так місячна вартість розмазується рівномірно, а не падає одним піком.
    """
    config.log.info("Starting scheduled traits refresh...")
    await refresh_stale_user_traits()


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

    # Schedule database cleanup at 04:00 AM Kyiv time
    cleanup_hour = 4
    app.job_queue.run_daily(
        cleanup_db_job,
        time=dtime(cleanup_hour, 0, tzinfo=config.KYIV),
        name="db_cleanup"
    )
    config.log.info(f"Database cleanup job scheduled for {cleanup_hour:02d}:00, {config.TZ}")

    # Після прибирання: працюємо вже по підчищеній таблиці messages.
    traits_hour = 5
    app.job_queue.run_daily(
        refresh_traits_job,
        time=dtime(traits_hour, 0, tzinfo=config.KYIV),
        name="traits_refresh",
    )
    config.log.info(
        f"Traits refresh job scheduled for {traits_hour:02d}:00, {config.TZ} "
        f"(profile TTL: {config.TRAITS_REFRESH_DAYS} days)"
    )
