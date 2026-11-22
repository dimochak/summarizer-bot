"""«save» у відповідь на картинку — бот надсилає її людині в особисті.

Telegram не дозволяє боту писати першим: людина має хоч раз відкрити бота й
натиснути Start. Якщо цього не було, бот відповідає в чаті посиланням, яке
відкриває особистий чат одним натиском.

Пересилаємо через copy_message: Telegram сам копіює медіа з підписом, без
повторного завантаження, і без позначки «переслано від».
"""
import random
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes, filters

import src.tools.config as config

# «save», «Save!», « SAVE. » — лише саме слово, щоб не ловити «save me» в розмові.
SAVE_PATTERN = re.compile(r"^\s*save\s*[!.]*\s*$", re.IGNORECASE)
SAVE_FILTER = filters.REPLY & filters.TEXT & filters.Regex(SAVE_PATTERN)

# Що вміємо зберігати. Текст — ні: його й так видно в чаті.
_MEDIA = ("photo", "animation", "video", "document", "sticker", "video_note")

START_PAYLOAD = "save"

# Службовий акаунт Telegram «Group» (GroupAnonymousBot): від його імені йдуть
# повідомлення анонімних адмінів. Їх зберігати не можна.
BLOCKED_AUTHOR_IDS = {1087968824}

# Для людей із SAVE_LOTTERY_USER_IDS save спрацьовує лише з цією імовірністю.
# Статична: ні виграш, ні програш її не змінюють.
LOTTERY_CHANCE = 46

LOTTERY_LOSE_PHRASES = [
    "Ха! Не цього разу 🎲 Ти в особливому списку: save для тебе спрацьовує з імовірністю {chance}%. Пробуй ще.",
    "Барабан крутиться… сектор «Пусто»! Картинка лишається в чаті, а ти — з носом. Твій шанс — {chance}%, і так буде завжди.",
    "Казино каже «ні» 🎰 Для обраних save — лотерея з шансом {chance}%, і тобі не випало.",
    "Мимо! Ти в моєму списку щасливчиків, яким щастить у {chance}% випадків. Кидай кубик ще.",
    "Не поїхала картинка 🫠 Особливий режим: успіх {chance}%. Не більше, не менше, назавжди.",
    "Лузер-мод активовано 🫵 Save для тебе працює з імовірністю {chance}%. Ще одна спроба?",
    "Пффф, ні. Ти ж знаєш, що ти в списку: шанс {chance}%. Удача любить наполегливих. Або ні.",
]


def _roll() -> float:
    return random.random()


def has_media(message) -> bool:
    return any(getattr(message, attr, None) for attr in _MEDIA)


async def _acknowledge(message) -> None:
    """Реакція на «save» замість повідомлення в чат: менше шуму."""
    try:
        await message.set_reaction("👍")
    except Exception as e:
        config.log.info(f"Save: не вдалось поставити реакцію: {e}")


async def on_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = msg.from_user if msg else None
    if msg is None or user is None or chat.id not in config.KNOWN_CHAT_IDS:
        return

    target = msg.reply_to_message
    if target is None or not has_media(target):
        return

    author = getattr(target, "from_user", None)
    if author is not None and author.id in BLOCKED_AUTHOR_IDS:
        config.log.info(f"Save: пост анонімного адміна в чаті {chat.id} — не зберігаємо")
        return

    if user.id in config.SAVE_LOTTERY_USER_IDS and _roll() * 100 >= LOTTERY_CHANCE:
        config.log.info(f"Save: лотерея — програш у чаті {chat.id} (шанс {LOTTERY_CHANCE}%)")
        await msg.reply_text(random.choice(LOTTERY_LOSE_PHRASES).format(chance=LOTTERY_CHANCE))
        return

    try:
        await context.bot.copy_message(
            chat_id=user.id, from_chat_id=chat.id, message_id=target.message_id
        )
    except Forbidden:
        # Людина ще не натискала Start або заблокувала бота.
        link = f"https://t.me/{context.bot.username}?start={START_PAYLOAD}"
        await msg.reply_text(
            f'Спершу відкрий мене в особистих: <a href="{link}">натисни Start</a>, '
            "а потім повтори save.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return
    except BadRequest as e:
        # Найчастіше — чат із забороною копіювання або повідомлення вже видалили.
        config.log.warning(f"Save: не вдалось скопіювати {target.message_id} у чаті {chat.id}: {e}")
        await msg.reply_text("Не вийшло зберегти: це повідомлення не можна скопіювати.")
        return

    config.log.info(f"Save: повідомлення {target.message_id} з чату {chat.id} надіслано в особисті")
    await _acknowledge(msg)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start в особистих. Після нього бот може надсилати людині збережене."""
    await update.effective_message.reply_text(
        "Готово. Тепер пиши save у відповідь на картинку в чаті — і я надішлю її сюди."
    )
