"""Збір постів і реакцій на них для топу в щоденному дайджесті.

Telegram не дає подивитись реакції на старі повідомлення: бот бачить лише
оновлення реакцій, які приходять уже після ввімкнення. Тому і пости, і
реакції записуємо самі, щойно вони зʼявляються.

Оновлення реакцій приходять, лише якщо бот — адміністратор чату.
"""
from datetime import timezone

from telegram import (
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
    ReactionTypePaid,
    Update,
)
from telegram.ext import ContextTypes

import src.tools.config as config
from src.tools.db import db_call, set_message_reactions, upsert_chat_post
from src.tools.utils import utc_ts

PREVIEW_LIMIT = 200

# Порядок важливий: у гіфки заповнене й поле document.
_KINDS = (
    ("photo", "photo"),
    ("animation", "animation"),
    ("video", "video"),
    ("video_note", "video_note"),
    ("voice", "voice"),
    ("audio", "audio"),
    ("sticker", "sticker"),
    ("document", "document"),
    ("poll", "poll"),
)


def post_kind(message) -> str | None:
    for attr, kind in _KINDS:
        if getattr(message, attr, None):
            return kind
    if getattr(message, "text", None):
        return "text"
    return None


def post_preview(message, kind: str) -> str | None:
    text = getattr(message, "text", None) or getattr(message, "caption", None)
    if not text and kind == "sticker":
        text = message.sticker.emoji
    if not text and kind == "poll":
        text = message.poll.question
    return text[:PREVIEW_LIMIT] if text else None


def reaction_key(reaction) -> str | None:
    if isinstance(reaction, ReactionTypeEmoji):
        return reaction.emoji
    if isinstance(reaction, ReactionTypeCustomEmoji):
        return f"custom:{reaction.custom_emoji_id}"
    if isinstance(reaction, ReactionTypePaid):
        return "paid"
    return None


def _utc(dt) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return utc_ts(dt.astimezone(timezone.utc))


async def register_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Записує кожне повідомлення чату. Працює в окремій групі хендлерів,
    тож не заважає основним: ті отримують те саме повідомлення як і раніше."""
    msg = update.effective_message
    chat = update.effective_chat
    if msg is None or chat is None or chat.id not in config.KNOWN_CHAT_IDS:
        return

    kind = post_kind(msg)
    if kind is None:
        return

    user = msg.from_user
    try:
        await db_call(
            upsert_chat_post,
            chat.id,
            msg.message_id,
            user.id if user else None,
            user.username if user else None,
            user.full_name if user else None,
            kind,
            post_preview(msg, kind),
            _utc(msg.date),
        )
    except Exception as e:
        # Топ у дайджесті — дрібниця; збій тут не має ламати обробку повідомлення.
        config.log.warning(f"Top posts: не вдалось записати пост {msg.message_id}: {e}")


async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reaction = update.message_reaction
    if reaction is None or reaction.chat.id not in config.KNOWN_CHAT_IDS:
        return

    # Анонімний адмін реагує від імені чату — тоді автор реакції це чат.
    if reaction.user is not None:
        actor_id = reaction.user.id
    elif reaction.actor_chat is not None:
        actor_id = reaction.actor_chat.id
    else:
        return

    keys = [k for k in (reaction_key(r) for r in reaction.new_reaction) if k]
    await db_call(
        set_message_reactions,
        reaction.chat.id,
        reaction.message_id,
        actor_id,
        keys,
        _utc(reaction.date),
    )


async def warn_if_not_admin(bot) -> None:
    """Без прав адміністратора Telegram не надсилає боту реакцій, і топ
    мовчки лишатиметься порожнім. Краще одразу побачити це в лозі."""
    for chat_id in sorted(config.ALLOWED_CHAT_IDS):
        try:
            member = await bot.get_chat_member(chat_id, bot.id)
        except Exception as e:
            config.log.warning(f"Top posts: не вдалось перевірити права в чаті {chat_id}: {e}")
            continue
        if member.status not in ("administrator", "creator"):
            config.log.warning(
                f"Top posts: бот не адміністратор у чаті {chat_id} — "
                f"реакцій не отримає, топ за реакціями буде порожній"
            )
