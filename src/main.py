from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters

import src.tools.config as config
from src.tools.db import init_db
from src.tools.handlers import (
    on_message,
    cmd_chatid,
    cmd_summary_now,
    cmd_enable_summaries,
    cmd_disable_summaries,
    cmd_status_summaries,
    cmd_find_all_pets, on_photo,
)
from src.tools.scheduler import schedule_daily


def main():
    init_db()

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

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
    app.add_handler(CommandHandler("petfinder", cmd_find_all_pets))

    schedule_daily(app)
    config.log.info("Bot started.")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
