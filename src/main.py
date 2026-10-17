from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

import src.tools.config as config
from src.alerts.air_raid import close_air_raid_client, schedule_air_raid
from src.reactions.react import log_reaction_assets
from src.tools.db import close_pool, init_db
from src.tools.handlers import (
    on_message,
    on_photo,
    on_voice,
    cmd_chatid,
    cmd_summary_now,
    cmd_enable_summaries,
    cmd_disable_summaries,
    cmd_status_summaries,
)
from src.tools.scheduler import schedule_daily
from src.top_posts.tracking import on_reaction, register_post, warn_if_not_admin

# Голосові й кружечки мають свій хендлер. Загальний їх раніше ковтав мовчки:
# тексту немає — on_message просто виходив. Виключаємо їх явно, а не покладаємось
# на порядок реєстрації: хендлери однієї групи працюють за першим збігом.
VOICE_FILTER = filters.VOICE | filters.VIDEO_NOTE
TEXT_FILTER = ~filters.StatusUpdate.ALL & ~filters.COMMAND & ~filters.PHOTO & ~VOICE_FILTER


async def post_init(application):
    bot_info = await application.bot.get_me()
    config.BOT_USER_ID = bot_info.id
    config.log.info(f"Bot initialized with ID: {config.BOT_USER_ID}")
    log_reaction_assets()
    await warn_if_not_admin(application.bot)


async def on_error(update, context):
    """Один рядок замість «No error handlers are registered» і стіни трейсбеку.

    Збій БД чи мережі валить кожне повідомлення, тож повний трейсбек на кожне
    лише ховає суть. Трейсбек лишаємо тільки для неочікуваних помилок.
    """
    error = context.error
    chat = getattr(getattr(update, "effective_chat", None), "id", None)
    if isinstance(error, (PoolTimeout, OperationalError)):
        config.log.error(f"DB unavailable (chat {chat}): {error}")
        return
    config.log.opt(exception=error).error(f"Unhandled error (chat {chat}): {error!r}")


async def post_shutdown(application):
    await close_air_raid_client(application)
    close_pool()
    config.log.info("Database pool closed.")


def main():
    init_db()

    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_error_handler(on_error)

    # Реєстр постів для топу за реакціями. Окрема група: хендлери різних груп
    # спрацьовують усі, тож основні хендлери нижче отримують те саме.
    app.add_handler(
        MessageHandler(~filters.StatusUpdate.ALL & ~filters.COMMAND, register_post),
        group=-1,
    )
    app.add_handler(
        MessageReactionHandler(
            on_reaction,
            message_reaction_types=MessageReactionHandler.MESSAGE_REACTION_UPDATED,
        )
    )

    app.add_handler(MessageHandler(TEXT_FILTER, on_message))
    app.add_handler(MessageHandler(VOICE_FILTER, on_voice))
    photo_or_image_doc_filter = filters.PHOTO | filters.Document.IMAGE
    app.add_handler(MessageHandler(photo_or_image_doc_filter, on_photo))

    app.add_handler(CommandHandler("chatid", cmd_chatid))

    app.add_handler(CommandHandler("summary_now", cmd_summary_now))
    app.add_handler(CommandHandler("enable_summaries", cmd_enable_summaries))
    app.add_handler(CommandHandler("disable_summaries", cmd_disable_summaries))
    app.add_handler(CommandHandler("status_summaries", cmd_status_summaries))

    schedule_daily(app)
    schedule_air_raid(app)
    config.log.info("Bot started.")

    # Явний перелік апдейтів замість «що Telegram запам'ятав минулого разу».
    # getUpdates без allowed_updates успадковує попереднє налаштування токена,
    # тож бот міг отримувати типи, яких ніхто не замовляв — зокрема реакції.
    #
    # edited_message свідомо НЕ включаємо: Update.effective_message його
    # покриває, тому редагування старого повідомлення повторно проходило
    # через on_message і бот відповідав на нього вдруге.
    #
    # message_reaction — для топу за реакціями. Сюди він іде лише в
    # MessageReactionHandler: effective_message у такого апдейту немає, тож
    # MessageHandler-и (і відповіді бота) на реакції не спрацьовують.
    app.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CHANNEL_POST, Update.MESSAGE_REACTION],
        close_loop=False,
    )


if __name__ == "__main__":
    main()
