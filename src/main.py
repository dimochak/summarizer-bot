from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters
)

import src.config as config
from src.db import init_db
from src.handlers import (
    on_message,
    cmd_chatid,
    cmd_summary_now,
    cmd_enable_summaries,
    cmd_disable_summaries,
    cmd_status_summaries,
)
from src.scheduler import schedule_daily


def main():
    init_db()

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(MessageHandler(~filters.StatusUpdate.ALL & ~filters.COMMAND, on_message))

    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("summary_now", cmd_summary_now))
    app.add_handler(CommandHandler("enable_summaries", cmd_enable_summaries))
    app.add_handler(CommandHandler("disable_summaries", cmd_disable_summaries))
    app.add_handler(CommandHandler("status_summaries", cmd_status_summaries))

    schedule_daily(app)
    config.log.info("Bot started.")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()