from datetime import datetime, timezone, time as dtime
from contextlib import closing
import random

from telegram import Update, Chat, Message
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import src.tools.config as config
from src.tools.db import (
    db,
    ensure_chat_record,
    add_message,
    upsert_photo_message,
    get_photo_messages_between,
    get_pet_messages_between,
    upsert_pet_photo,
    get_duplicate_photo_message_id,
    get_panbot_usage,
    increment_panbot_usage,
    is_bot_message,
    get_custom_role,
    set_custom_role
)
from src.panbot.engine.core import PanBotEngine
from src.panbot.engine.summary import SummaryEngine
from src.panbot.helpers import (
    BOT_TRIGGERS,
    should_reply,
    check_summary_request,
    get_quoted_block,
    get_traits_block,
)
from src.panbot.formatting import format_telegram_html
from src.panbot.exceptions import SarcasmLimitExceeded
from src.summarizer.summarizer import summarize_day
from src.petfinder.pets import detect_and_caption_by_file_id, PET_CONFIDENCE_THRESHOLD
from src.tools.utils import utc_ts, local_midnight_bounds, message_link
from src.websearch.search import FAS_PATTERN, search_web, summarize_results

SEARCH_PLACEHOLDERS = [
    "🔍 Зараз, зараз... Полізу в інтернет, бо своїх мізків не вистачає на таке.",
    "🌐 О, ви хочете, щоб я ще й гуглив за вас? Ну добре, чекайте...",
    "🔎 Запускаю пошук... Сподіваюся, результат буде розумнішим за запит.",
    "🧠 Мої нейрони перенаправляються в інтернет. Тримайтесь.",
    "🕵️ Йду шукати. Якщо не повернусь — шукайте мене в кеші Google.",
    "📡 Підключаюсь до всесвітньої павутини. Павуки вже чекають.",
]

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

SKIP_REPLY_MESSAGES = [
    "👍 Іронічний лайк за думку. Наступного разу спробуй здивувати сильніше.",
    "😏 Ставлю уявний лайк. І ні, відповідати більше не буду.",
    "🤏 Ось тобі міні-реакція, бо повноцінна відповідь тут зайва.",
    "🫡 Зараховано. Реакція є, сенсу — як завжди, на мінімалках.",
    "🙃 Іронічно схвалюю. Без продовження, щоб не зіпсувати момент.",
]

panbot_engine = PanBotEngine(debug=config.LANGCHAIN_DEBUG)
summary_engine = SummaryEngine()


async def should_reply_with_agent(message: Message) -> bool | None:
    if not should_reply(message):
        return None

    raw_text = message.text or message.caption or ""
    text = raw_text.lower()
    has_trigger = any(trigger in text for trigger in BOT_TRIGGERS)

    reply_to_text = None
    is_reply_to_bot = False
    if getattr(message, "reply_to_message", None):
        reply_to_text = message.reply_to_message.text or ""
        chat_id = message.chat.id if getattr(message, "chat", None) else None
        reply_to_id = message.reply_to_message.message_id
        if chat_id and reply_to_id:
            is_reply_to_bot = is_bot_message(chat_id, reply_to_id)

    try:
        return await panbot_engine.should_reply_by_agent(
            message=message,
            user_message=raw_text,
            reply_to_text=reply_to_text,
            is_reply_to_bot=is_reply_to_bot,
            has_trigger=has_trigger,
        )
    except Exception as e:
        config.log.exception("Reply agent failed, fallback to should_reply: %s", e)
        return True

FALLBACK_RESPONSES = [
    "О, у мене технічні проблеми! Як символічно для нашого розмови 🙄",
    "Мій штучний інтелект відмовляється працювати з таким рівнем запитань 🤖",
    "Вибачте, але моя іронія зараз на технічному обслуговуванні ⚙️",
    "Схоже, навіть комп'ютери можуть втомлюватися від людської нелогічності 😴",
    "Error 404: Сарказм не знайдено. Спробуйте розумніше питання 🔍",
]

CREATOR_USER_ID = 229953580
CUSTOM_ROLE_TRIGGER = "ботяндра, твоя нова роль"


def _append_quota_notice(response: str, remaining: int) -> str:
    """Попереджає про вичерпання денної норми на останніх спробах."""
    if remaining > 1:
        return response
    if remaining <= 0:
        return response + (
            "\n\n⚠️ Це ваша остання спроба поспілкуватись зі мною на сьогодні. "
            "Адьйос, пасажири!"
        )
    return response + f"\n\n💡 У вас залишилося {remaining} спроб на сьогодні."


async def get_panbot_response(message: Message) -> str:
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    today = datetime.now(tz=config.KYIV).date().isoformat()
    daily_limit = config.MESSAGES_PER_USER

    current_usage = get_panbot_usage(user_id, chat_id, today)
    if current_usage >= daily_limit:
        raise SarcasmLimitExceeded(
            f"Ви вже вичерпали свою денну норму сарказму ({daily_limit} разів). "
            f"Спробуйте завтра, можливо, до того часу ваші питання стануть розумнішими! 🙄"
        )

    user_message = message.text or ""
    user_name = message.from_user.full_name if message.from_user else "Невідомий пасажир"

    # Спроба вважається витраченою лише тоді, коли користувач отримав
    # осмислену відповідь. Технічний збій квоту не з'їдає.
    charge_quota = True

    summary_match = check_summary_request(user_message)
    if summary_match:
        try:
            response = await summary_engine.get_summary(
                chat_id=chat_id,
                request_type=summary_match["type"],
                value=summary_match["value"],
            )
        except Exception as e:
            config.log.exception("Error handling summary request: %s", e)
            response = "Не зміг підсумувати ваші бредні, спробуйте пізніше 🙄"
            charge_quota = False
    else:
        quoted_block = get_quoted_block(message)
        traits_block = get_traits_block(user_id)
        is_creator = user_id == CREATOR_USER_ID

        if is_creator and user_message.lower().startswith(CUSTOM_ROLE_TRIGGER):
            new_role = user_message[len(CUSTOM_ROLE_TRIGGER):].strip()
            set_custom_role(chat_id, new_role or None)
            response = (
                f"Слухаюсь, батьку! Тепер моя роль: {new_role}"
                if new_role
                else "Слухаюсь, батьку! Кастомну роль скинуто до стандартної."
            )
            new_count = increment_panbot_usage(user_id, chat_id, today)
            return _append_quota_notice(response, daily_limit - new_count)

        custom_role = get_custom_role(chat_id)

        try:
            config.log.info(f"Generating response for chat {chat_id}")
            response = await panbot_engine.generate_response(
                message=message,
                quoted_block=quoted_block,
                traits_block=traits_block,
                user_name=user_name,
                user_message=user_message,
                custom_role=custom_role,
                is_creator=is_creator,
            )
        except Exception as e:
            config.log.exception("Error generating response: %s", e)
            response = random.choice(FALLBACK_RESPONSES)
            charge_quota = False

    if not charge_quota:
        return response

    new_count = increment_panbot_usage(user_id, chat_id, today)
    return _append_quota_notice(response, daily_limit - new_count)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.effective_message
    chat: Chat = update.effective_chat

    # Бот працює в чаті, якщо той налаштований або на підсумки, або на PanBot
    if chat.id not in config.KNOWN_CHAT_IDS:
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
        str(text) if text is not None else None,
        (msg.reply_to_message and msg.reply_to_message.message_id) or None,
        utc_ts(ts.astimezone(timezone.utc)),
    )

    # Check for "ботяндра, <query>, фас" search pattern
    fas_match = FAS_PATTERN.search(text) if text else None
    if fas_match:
        query = fas_match.group(1).strip()
        if query:
            placeholder = await msg.reply_text(random.choice(SEARCH_PLACEHOLDERS))
            try:
                results = await search_web(query)
                answer = await summarize_results(query, results, chat.id)
                await placeholder.edit_text(
                    answer, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            except Exception as e:
                config.log.exception(f"Web search failed: {e}")
                await placeholder.edit_text(
                    "Щось пішло не так під час пошуку. "
                    "Можливо, інтернет теж втомився від ваших запитів 🤷‍♂️"
                )
            return

    # Check if PanBot should reply to this message
    if chat.id in config.PANBOT_CHAT_IDS:
        reply_decision = await should_reply_with_agent(msg)
        if reply_decision is None:
            return

        try:
            if reply_decision:
                response = await get_panbot_response(msg)
            else:
                # Рішення НЕ відповідати не повинно коштувати повного виклику LLM:
                # раніше воно генерувало відмову тією ж моделлю, що й справжню
                # відповідь, і при цьому не списувало квоту.
                response = random.choice(SKIP_REPLY_MESSAGES)
            # Ensure response is a string before replying and storing
            response_str = str(response) if response is not None else ""
            formatted_response = format_telegram_html(response_str)
            bot_message = await msg.reply_text(formatted_response, parse_mode=ParseMode.HTML)
            bot_ts = bot_message.date
            if bot_ts.tzinfo is None:
                bot_ts = bot_ts.replace(tzinfo=timezone.utc)
            add_message(
                chat.id,
                bot_message.message_id,
                config.BOT_USER_ID,
                None,
                "PanBot",
                response_str,
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
    msg = update.message or update.channel_post
    if not msg or not update.effective_chat:
        return

    chat = update.effective_chat

    config.log.info(f"Triggered on photo: chat {chat.id} msg {msg.message_id}")

    try:
        ensure_chat_record(chat)
    except Exception as e:
        config.log.exception(f"ensure_chat_record failed: {e}")

    ts = msg.date or datetime.now(timezone.utc)

    if msg.photo:
        largest = msg.photo[-1]
        file_id = largest.file_id
        file_unique_id = largest.file_unique_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        file_id = msg.document.file_id
        file_unique_id = msg.document.file_unique_id
    else:
        config.log.warning(f"on_photo -- neither photo nor image document: message_id {msg.message_id}")
        return

    config.log.info(f"on_photo: file_unique_id={file_unique_id} in chat {chat.id}")

    ts_utc_int = utc_ts(ts)

    # First, store the photo message to ensure it's in the DB
    try:
        upsert_photo_message(
            chat_id=chat.id,
            message_id=msg.message_id,
            ts_utc=ts_utc_int,
            file_id=file_id,
            file_unique_id=file_unique_id,
        )
        config.log.info(f"Photo/document stored: chat {chat.id} msg {msg.message_id}")
    except Exception as e:
        config.log.exception("upsert_photo_message failed: %s", e)

    # Check for duplicates if PanBot is enabled in this chat
    if chat.id in config.PANBOT_CHAT_IDS:
        # We look for a duplicate, excluding the current message
        orig_msg_id = get_duplicate_photo_message_id(chat.id, file_unique_id, exclude_message_id=msg.message_id)
        if orig_msg_id:
            config.log.info(f"Duplicate photo detected in chat {chat.id}, file_unique_id {file_unique_id}, original message_id {orig_msg_id}")
            try:
                # Use a special prompt for the engine to generate a sarcastic comment about the duplicate (banka/bayan)
                user_name = msg.from_user.full_name if msg.from_user else "Невідомий пасажир"
                custom_role = get_custom_role(chat.id)
                user_id = msg.from_user.id if msg.from_user else 0
                is_creator = (user_id == 229953580)

                orig_link = message_link(chat, orig_msg_id)
                fake_msg = (
                    f"Ця картинка вже була ось тут: {orig_link}\n"
                    "Це 'банка' (баян)! Дай іронічно-саркастичний коментар, ОБОВ'ЯЗКОВО встав посилання на оригінал у свій текст "
                    "та нагадай про посилання на банку для донатів: https://send.monobank.ua/jar/6BjaNq1d5B"
                )
                
                response = await panbot_engine.generate_response(
                    message=msg,
                    quoted_block="",
                    traits_block=get_traits_block(user_id),
                    user_name=user_name,
                    user_message=fake_msg,
                    custom_role=custom_role,
                    is_creator=is_creator
                )
                formatted_response = format_telegram_html(response)

                # Send the response directly from LLM without manual link appending
                bot_message = await msg.reply_text(formatted_response, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                config.log.info(f"Duplicate response sent: chat {chat.id} msg {bot_message.message_id}")
                
                # Add bot response to message history
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
            except Exception as e:
                config.log.exception(f"Error generating duplicate photo response: {e}")




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
    Command: /petfinder
    Fetch today's photos from DB, run detection on unseen ones, cache results, and return links.
    Now sends a placeholder and uses LLM to generate short ironic captions for each detected pet.
    """
    if not update.effective_chat or not update.effective_user:
        return
    chat = update.effective_chat

    placeholder_texts = [
        "⏳ Аналізую галерею: пес вже зголоднів від очікування, але тримається як чемпіон.",
        "🧐 Збираю дос'є на хвостатих: головний підозрюваний — пес, мотив — печиво.",
        "🔍 Перевіряю фото на наявність псячого ентузіазму — рівень зашкалює, як завжди.",
        "🐾 Відслідковую сліди лап до миски — сліди свіжі, справа очевидна.",
        "📦 Розпаковую пакет з «хто хороший хлопчик?» — відповідь передбачувана.",
        "🧭 Навожу фокус на песика: він навів фокус на повідець і має плани.",
        "🧪 Тест на «добрий пес» пройдено: показники підскакують при слові «прогулянка».",
        "🏷️ Звіряю ярлики: «гав», «ще раз гав», «а тепер за смаколик».",
        "🧊 Охолоджую камеру — пес надто гарячий до уваги і камери.",
        "🎛️ Підкручую повзунки слухняності — ага, звісно, як тільки з’явиться білка.",
        "🧩 Складаю пазл з пікселів: шматок із вухами знайшовся біля дверей.",
        "🧮 Порахував подихи щастя — калькулятор попросив перерву.",
        "🧱 Якщо це пес, то він — фортеця на лапах: охороняє, але впустить за смаколик.",
        "🏛️ Передаю справу до Верховного Пес-суду: вирок — «ще одну прогулянку».",
        "🧿 Перевіряю на магію: пес знову змусив усіх усміхнутися — підозріло ефективно.",
        "🧪 Аналіз показує: 90% радість, 10% дуже терміново треба на вулицю.",
        "🧰 Калібрую детектор «хороший хлопчик/дівчинка» — стрілка уперлася вправо.",
        "🪪 Ідентифікую власника: пес володіє настроєм, ви — повідцем.",
        "🧬 Розшифровую ДНК погляду: «я нічого не робив, але раптом печиво?»",
        "🧭 Маршрут простий: від «хто це?» до «де мій м’яч і ще 200 фото».",
        "🧵 Розмотую клубок доказів — кіт уже сидить зверху і судить нас поглядом.",
        "🧩 Останній шматок пазлу зник — кіт з’їв його репутаційно.",
        "🏷️ Котяча версія ярликів: «мур», «ігнор», «обмірковую переворот».",
        "🧊 Камера розплавилася від котячої зневаги — аварійне охолодження ввімкнено.",
        "🎛️ Повзунок зверхності на максимум — кіт схвалив. Мовчки.",
        "📡 «Мяу-FM» в ефірі: ведучий знову оголошує нас обслугою.",
    ]
    placeholder_message = await update.message.reply_text(random.choice(placeholder_texts))

    now_local = datetime.now(config.KYIV)
    start_local, end_local = local_midnight_bounds(now_local)
    start_ts = utc_ts(start_local.astimezone(timezone.utc))
    end_ts = utc_ts(end_local.astimezone(timezone.utc))

    try:
        photos = get_photo_messages_between(chat.id, start_ts, end_ts)
    except Exception as e:
        config.log.exception(f"get_photo_messages_between failed: {e}")
        await placeholder_message.edit_text("Сталася помилка при отриманні фотографій.")
        return

    if not photos:
        await placeholder_message.edit_text("За сьогодні фото не надсилали.")
        return

    try:
        detected = get_pet_messages_between(chat.id, start_ts, end_ts)
    except Exception as e:
        config.log.exception(f"get_photo_messages_between failed: {e}")
        detected = []

    detected_by_id = {(r["chat_id"], r["message_id"]): r for r in detected}
    results_lines: list[str] = []

    for r in detected:
        if r["species"] in ("cat", "dog"):
            link = message_link(chat, r["message_id"])
            desc = None
            file_id = None
            try:
                file_id = r.get("file_id") if isinstance(r, dict) else None
            except Exception as e:
                config.log.exception(f"Failure: {e}")
                pass

            if file_id:
                try:
                    _, _, caption = await detect_and_caption_by_file_id(context, file_id, sarcasm_level=5)
                    desc = (caption or "").strip() or None
                except Exception as e:
                    config.log.exception(f'detect_and_caption failed for cached {r["chat_id"]}: {r["message_id"]}: {e}')

            if not desc:
                label = "кіт" if r["species"] == "cat" else "пес"
                desc = f"{label} ({r['confidence']:.2f})"

            results_lines.append(f"• {desc} — {link}")

    for p in photos:
        key = (p["chat_id"], p["message_id"])
        if key in detected_by_id:
            continue  # already processed

        try:
            species, conf, caption = await detect_and_caption_by_file_id(context, p["file_id"], sarcasm_level=5)
        except Exception as e:
            config.log.exception(f"photo detection failed for {key}: {e}")
            continue

        if species in ("cat", "dog") and conf >= PET_CONFIDENCE_THRESHOLD:
            created_at_utc = utc_ts(datetime.now(timezone.utc))
            try:
                upsert_pet_photo(
                    chat_id=p["chat_id"],
                    message_id=p["message_id"],
                    ts_utc=p["ts_utc"],
                    species=species,
                    confidence=conf,
                    file_id=p["file_id"],
                    created_at_utc=created_at_utc,
                )
            except Exception as e:
                config.log.exception(f"upsert_pet_photo failed: {e}")

            desc = (caption or "").strip()
            if not desc:
                label = "кіт" if species == "cat" else "пес"
                desc = f"{label} ({conf:.2f})"

            link = message_link(chat, p["message_id"])
            results_lines.append(f"• {desc} — {link}")

    if not results_lines:
        await placeholder_message.edit_text("За сьогодні фото котів чи собак не знайдено.")
        return

    text = "Знайдені фото за сьогодні:\n" + "\n".join(results_lines)
    await placeholder_message.edit_text(text, disable_web_page_preview=True)

