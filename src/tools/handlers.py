from datetime import datetime, timezone, time as dtime
from contextlib import closing
import random

from telegram import Update, Chat, Message
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import src.tools.config as config
from src.tools.db import db, ensure_chat_record, add_message, upsert_photo_message, get_photo_messages_between, \
    get_pet_messages_between
from src.panbot.bot import PanBot, SarcasmLimitExceeded
from src.summarizer.summarizer import summarize_day
from src.tools.pets import _download_file_bytes, detect_pet_species, PET_CONFIDENCE_THRESHOLD
from src.tools.utils import utc_ts, local_midnight_bounds, message_link

INITIAL_PLACEHOLDERS = [
    "⏳ Окей, я подивлюся, що ви там набазікали. Тільки не очікуйте нічого геніального.",
    "🧐 Викликали? Навіщо? Ну добре, зараз спробую знайти хоч одну розумну думку у вашому чаті.",
    "🤖 Запускаю аналіз вашого словесного потоку. Не заздрю собі.",
    "⏳ Зараз, зараз, дай переварити все це сміття, що ви називаєте розмовою.",
    "🙄 Дайте вгадаю: знову флейм про те, хто кращий у своїй справі?",
    "😴 О, ще одна порція ваших 'глибоких' роздумів. Зараз розберемося.",
    "🤦‍♂️ Підсумки дня від людей, які не можуть підсумувати власні думки.",
    "⌛ Терплячка лопнула, але я все одно спробую знайти сенс у цьому хаосі.",
    "🎭 Драматичні повороти сюжету! Хто сьогодні кого образив?",
    "🔍 Шукаю інтелект у вашому чаті. Поки безрезультатно.",
    "🤷‍♀️ Ну що, знову будемо робити вигляд, що це була змістовна дискусія?",
    "📊 Статистика дня: 90% емоцій, 10% фактів. Як завжди.",
    "🎪 Цирк приїхав! Зараз подивимося, хто сьогодні був головним клоуном.",
    "😅 Ваш рівень аргументації стабільно вражає. У негативному сенсі.",
    "🤏 Спробую видавити хоч краплину мудрості з цього океану словесного спаму.",
    "🎯 Цікаво, скільки разів ви сьогодні минули суть повз вуха?",
]

panbot = PanBot(daily_limit=config.MESSAGES_PER_USER)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.effective_message
    chat: Chat = update.effective_chat

    # Check if this chat is allowed (either Gemini or OpenAI)
    if chat.id not in config.ALLOWED_CHAT_IDS:
        return

    ensure_chat_record(chat)

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
        utc_ts(ts.astimezone(timezone.utc)),
    )

    # Check if PanBot should reply to this message
    if chat.id in config.PANBOT_CHAT_IDS and panbot.should_reply(msg):
        try:
            response = await panbot.process_reply(msg)
            bot_message = await msg.reply_text(response, parse_mode=ParseMode.HTML)
            bot_ts = bot_message.date
            if bot_ts.tzinfo is None:
                bot_ts = bot_ts.replace(tzinfo=timezone.utc)
            add_message(
                chat.id,
                bot_message.message_id,
                config.BOT_USER_ID,
                None,
                "PanBot",
                response,
                msg.message_id,
                utc_ts(bot_ts.astimezone(timezone.utc)),
            )

        except SarcasmLimitExceeded as e:
            await msg.reply_text(str(e))

        except Exception as e:
            config.log.exception(f"Error in PanBot response: {e}")
            await msg.reply_text(
                "Щось пішло не так з моїм сарказмом... "
                "Можливо, ваше питання було занадто складним для мого штучного інтелекту 🤖"
            )


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for any photo (image) message in chats where the bot is present.
    Now supports both photo and image document uploads!
    """
    if not update.message or not update.effective_chat:
        return

    chat = update.effective_chat
    msg = update.message

    config.log.info(f"Triggered on photo: chat {chat.id} msg {msg.message_id}")

    try:
        ensure_chat_record(chat)
    except Exception as e:
        config.log.exception("ensure_chat_record failed: %s", e)

    ts = msg.date or datetime.now(timezone.utc)

    if msg.photo:
        largest = msg.photo[-1]
        file_id = largest.file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        file_id = msg.document.file_id
    else:
        config.log.warning("on_photo -- neither photo nor image document: message_id %s", msg.message_id)
        return

    ts_utc_int = utc_ts(ts)

    try:
        upsert_photo_message(
            chat_id=chat.id,
            message_id=msg.message_id,
            ts_utc=ts_utc_int,
            file_id=file_id,
        )
        config.log.info("Photo/document stored for deferred detection: chat %s msg %s", chat.id, msg.message_id)
    except Exception as e:
        config.log.exception("upsert_photo_message failed: %s", e)



async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    provider_info = []
    if chat_id in config.OPENAI_CHAT_IDS:
        provider_info.append("OpenAI ✅")
    if chat_id in config.GEMINI_CHAT_IDS:
        provider_info.append("Gemini ✅")
    if chat_id in config.PANBOT_CHAT_IDS:
        provider_info.append("PanBot ✅")

    if not provider_info:
        provider_info = ["❌ Not configured"]

    await update.effective_message.reply_html(
        f"<code>{chat_id}</code>\nServices: <b>{', '.join(provider_info)}</b>"
    )


async def cmd_summary_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    # Check if this chat is allowed (either Gemini or OpenAI)
    if chat.id not in config.ALLOWED_CHAT_IDS:
        await update.effective_message.reply_text(
            "❌ Цей чат не налаштовано для використання AI-підсумків.\n"
            "Зверніться до адміністратора бота."
        )
        return

    # Parse toxicity level from command arguments
    toxicity_level = 9  # Default to maximum toxicity

    if context.args:
        try:
            toxicity_level = int(context.args[0])
            if not (0 <= toxicity_level <= 9):
                await update.effective_message.reply_text(
                    "❌ Рівень токсичності має бути від 0 (дружелюбний) до 9 (максимально токсичний)."
                )
                return
        except ValueError:
            await update.effective_message.reply_text(
                "❌ Невірний формат. Використовуйте: /summary_now [0-9]\n"
                "0 = дружелюбний стиль, 9 = максимально токсичний стиль."
            )
            return

    # Choose appropriate placeholder based on toxicity level
    if toxicity_level <= 2:
        placeholder_messages = [
            "⏳ Хвилинку, аналізую ваші повідомлення...",
            "🤔 Зараз подивлюся, що цікавого було сьогодні в чаті.",
            "📝 Готую підсумок дня для вас!",
        ]
    elif toxicity_level <= 5:
        placeholder_messages = [
            "⏳ Ну добре, зараз розберемося з вашими розмовами...",
            "🧐 Спробую знайти щось осмислене у вашому чаті.",
            "📊 Аналізую ваші словесні потоки...",
        ]
    else:
        placeholder_messages = INITIAL_PLACEHOLDERS

    # Send a placeholder message first to acknowledge the command
    placeholder_message = await update.effective_message.reply_html(
        random.choice(placeholder_messages)
    )

    # Perform the long-running summary generation
    now_local = datetime.now(tz=config.KYIV)
    start_local = datetime.combine(
        now_local.date(), dtime.min, tzinfo=config.KYIV
    )  # сьогодні від 00:00
    text = await summarize_day(chat, start_local, now_local, context, toxicity_level)

    # Prepare the final text
    if not text:
        text = "<b>#Підсумки_дня — сьогодні</b>\n\nПоки що немає даних або нічого не згрупувалося."

    # Edit the placeholder message with the final summary
    await placeholder_message.edit_text(
        text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_enable_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    # Check if this chat is allowed (either Gemini or OpenAI)
    if chat.id not in config.ALLOWED_CHAT_IDS:
        await update.effective_message.reply_text(
            "❌ Цей чат не налаштовано для використання AI-підсумків.\n"
            "Зверніться до адміністратора бота."
        )
        return

    ensure_chat_record(chat)
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("UPDATE chats SET enabled=1 WHERE chat_id=%s", (chat.id,))
        conn.commit()
    await update.effective_message.reply_text(
        "✅ Daily summaries enabled for this chat."
    )


async def cmd_disable_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    # Check if this chat is allowed (either Gemini or OpenAI)
    if chat.id not in config.ALLOWED_CHAT_IDS:
        await update.effective_message.reply_text(
            "❌ Цей чат не налаштовано для використання AI-підсумків.\n"
            "Зверніться до адміністратора бота."
        )
        return

    ensure_chat_record(chat)
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("UPDATE chats SET enabled=0 WHERE chat_id=%s", (chat.id,))
        conn.commit()
    await update.effective_message.reply_text(
        "🚫 Daily summaries disabled for this chat."
    )


async def cmd_status_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    # Determine AI provider and configuration status
    if chat.id in config.OPENAI_CHAT_IDS:
        provider_status = "OpenAI ✅"
    elif chat.id in config.GEMINI_CHAT_IDS:
        provider_status = "Gemini ✅"
    else:
        provider_status = "❌ Not configured"

    # Check if summaries are enabled in database
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT enabled FROM chats WHERE chat_id=%s", (chat.id,))
        row = cur.fetchone()
    enabled = row and row["enabled"] == 1

    status_text = (
        f"**Configuration Status:**\n"
        f"AI Provider: {provider_status}\n"
        f"Daily Summaries: {'ENABLED ✅' if enabled else 'DISABLED 🚫'}"
    )

    await update.effective_message.reply_text(status_text)

async def cmd_find_all_pets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Command: /find_all_pets
    Fetch today's photos from DB, run detection on unseen ones, cache results, and return links.
    """
    if not update.effective_chat or not update.effective_user:
        return
    chat = update.effective_chat

    # Compute local-day bounds, convert to UTC timestamps
    now_local = datetime.now(config.KYIV)
    start_local, end_local = local_midnight_bounds(now_local)
    start_ts = utc_ts(start_local.astimezone(timezone.utc))
    end_ts = utc_ts(end_local.astimezone(timezone.utc))

    # Get all photo messages for today
    try:
        photos = get_photo_messages_between(chat.id, start_ts, end_ts)
    except Exception as e:
        config.log.exception("get_photo_messages_between failed: %s", e)
        await update.message.reply_text("Сталася помилка при отриманні фотографій.")
        return

    if not photos:
        await update.message.reply_text("За сьогодні фото не надсилали.")
        return

    # Get already detected pets for today (cache)
    try:
        detected = get_pet_messages_between(chat.id, start_ts, end_ts)
    except Exception as e:
        config.log.exception("get_pet_messages_between failed: %s", e)
        detected = []

    detected_by_id = {(r["chat_id"], r["message_id"]): r for r in detected}
    results_links: list[str] = []

    # First, include already detected cat/dog
    for r in detected:
        if r["species"] in ("cat", "dog"):
            link = message_link(chat, r["message_id"])
            label = "кіт" if r["species"] == "cat" else "пес"
            results_links.append(f"• {label} ({r['confidence']:.2f}) — {link}")

    # Process only photos without a cached detection
    for p in photos:
        key = (p["chat_id"], p["message_id"])
        if key in detected_by_id:
            continue  # already processed

        try:
            img_bytes = await _download_file_bytes(context, p["file_id"])
        except Exception as e:
            config.log.exception("photo download failed for %s: %s", key, e)
            continue

        species, conf = await detect_pet_species(img_bytes)
        if species in ("cat", "dog") and conf >= PET_CONFIDENCE_THRESHOLD:
            created_at_utc = utc_ts(datetime.now(timezone.utc))
            try:
                db.upsert_pet_photo(
                    chat_id=p["chat_id"],
                    message_id=p["message_id"],
                    ts_utc=p["ts_utc"],
                    species=species,
                    confidence=conf,
                    file_id=p["file_id"],
                    created_at_utc=created_at_utc,
                )
            except Exception as e:
                config.log.exception("upsert_pet_photo failed: %s", e)
                # even if DB write failed, we can still display the link
            link = message_link(chat, p["message_id"])
            label = "кіт" if species == "cat" else "пес"
            results_links.append(f"• {label} ({conf:.2f}) — {link}")

    if not results_links:
        await update.message.reply_text("За сьогодні фото котів чи собак не знайдено.")
        return

    text = "Знайдені фото за сьогодні:\n" + "\n".join(results_links)
    await update.message.reply_text(text, disable_web_page_preview=True)
