from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters

import src.tools.config as config
from src.tools.db import close_pool, init_db
from src.tools.handlers import (
    on_message,
    on_photo,
    cmd_chatid,
    cmd_summary_now,
    cmd_enable_summaries,
    cmd_disable_summaries,
    cmd_status_summaries,
)
from src.tools.scheduler import schedule_daily


async def post_init(application):
    bot_info = await application.bot.get_me()
    config.BOT_USER_ID = bot_info.id
    config.log.info(f"Bot initialized with ID: {config.BOT_USER_ID}")


async def post_shutdown(application):
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

    app.add_handler(
        MessageHandler(~filters.StatusUpdate.ALL &
                       ~filters.COMMAND &
                       ~filters.PHOTO, on_message)
    )
    photo_or_image_doc_filter = filters.PHOTO | filters.Document.IMAGE
    app.add_handler(MessageHandler(photo_or_image_doc_filter, on_photo))

    app.add_handler(CommandHandler("chatid", cmd_chatid))

    app.add_handler(CommandHandler("summary_now", cmd_summary_now))
    app.add_handler(CommandHandler("enable_summaries", cmd_enable_summaries))
    app.add_handler(CommandHandler("disable_summaries", cmd_disable_summaries))
    app.add_handler(CommandHandler("status_summaries", cmd_status_summaries))

    schedule_daily(app)
    config.log.info("Bot started.")

    # Явний перелік апдейтів замість «що Telegram запам'ятав минулого разу».
    # getUpdates без allowed_updates успадковує попереднє налаштування токена,
    # тож бот міг отримувати типи, яких ніхто не замовляв — зокрема реакції.
    #
    # edited_message свідомо НЕ включаємо: Update.effective_message його
    # покриває, тому редагування старого повідомлення повторно проходило
    # через on_message і бот відповідав на нього вдруге.
    app.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CHANNEL_POST],
        close_loop=False,
    )


if __name__ == "__main__":
    main()
