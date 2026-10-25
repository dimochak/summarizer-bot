"""Реакції-картинки на згадки певних тем (наприклад, Кличка).

Потік:
    регулярка -> кулдаун? -> є картинки? -> LLM-підтвердження -> атомарний
    захват слоту -> [підпис від LLM, якщо правило його хоче] -> картинка

Дешеві перевірки стоять першими, щоб модель кликалась якомога рідше.
"""
import asyncio
import random
from datetime import timezone
from pathlib import Path
from time import time

from pydantic import BaseModel, Field
from telegram import MessageEntity
from telegram.constants import ParseMode

import src.tools.config as config
from src.core.llm import get_structured_llm
from src.panbot.engine.core import PanBotEngine
from src.panbot.formatting import format_telegram_html
from src.panbot.helpers import get_traits_block
from src.reactions.rules import MENTION_RULES, RULES, ReactionRule
from src.tools.db import (
    add_message,
    claim_reaction,
    db_call,
    get_custom_role,
    is_reaction_on_cooldown,
)
from src.tools.utils import utc_ts

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ANIMATION_EXTENSIONS = {".gif", ".mp4"}

# Telegram обмежує підпис до медіа 1024 символами вже ПІСЛЯ розбору HTML.
# Беремо із запасом, щоб теги форматування не виштовхнули текст за межу.
CAPTION_LIMIT = 900

_engine = PanBotEngine(debug=config.LANGCHAIN_DEBUG)

# Сильні посилання на фонові задачі: asyncio тримає лише слабкі, і задача без
# посилання може бути прибрана збирачем сміття посеред виконання.
_background: set[asyncio.Task] = set()


class SubjectMention(BaseModel):
    is_about: bool = Field(description="Чи справді повідомлення стосується цієї теми")


_MENTION_TYPES = [MessageEntity.MENTION, MessageEntity.TEXT_MENTION]


def tagged_users(message) -> tuple[set[int], set[str]]:
    """Кого тегнули в повідомленні: (ID, юзернейми в нижньому регістрі).

    @username дає лише юзернейм. Тег людини без юзернейма (вибір зі списку
    учасників) приходить як text_mention з ID. Самотег не рахується.
    """
    ids: set[int] = set()
    usernames: set[str] = set()
    if message is None:
        return ids, usernames

    found = {}
    for parse in ("parse_entities", "parse_caption_entities"):
        method = getattr(message, parse, None)
        if callable(method):
            found.update(method(_MENTION_TYPES))

    for entity, value in found.items():
        if entity.type == MessageEntity.TEXT_MENTION and entity.user:
            ids.add(entity.user.id)
        elif entity.type == MessageEntity.MENTION:
            usernames.add(value.lstrip("@").lower())

    author = getattr(message, "from_user", None)
    if author is not None:
        ids.discard(author.id)
        if getattr(author, "username", None):
            usernames.discard(author.username.lower())
    return ids, usernames


def _rules() -> list[ReactionRule]:
    return RULES + MENTION_RULES


def matching_rules(text: str | None, message=None) -> list[ReactionRule]:
    matched = []
    tagged = None
    for rule in _rules():
        if rule.is_mention:
            if tagged is None:
                tagged = tagged_users(message)
            ids, usernames = tagged
            if rule.mention_user_ids & ids or rule.mention_usernames & usernames:
                matched.append(rule)
        elif text and rule.pattern is not None and rule.pattern.search(text):
            matched.append(rule)
    return matched


def list_media(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS | ANIMATION_EXTENSIONS
    ]


def pick_media(directory: Path) -> Path | None:
    candidates = list_media(directory)
    return random.choice(candidates) if candidates else None


def pick_image(rule: ReactionRule) -> Path | None:
    return pick_media(rule.images_dir)


async def confirm_mention(rule: ReactionRule, text: str, chat_id: int) -> bool:
    """Чи справді йдеться про тему, а не про випадковий збіг регулярки."""
    llm = get_structured_llm(SubjectMention, chat_id=chat_id, purpose="decision")
    prompt = (
        f"Чи йдеться в цьому повідомленні про: {rule.subject}?\n"
        f"{rule.exclusions}\n\n"
        f"Повідомлення:\n«{text[:2000]}»"
    )
    result = await llm.ainvoke(prompt)
    return bool(result.is_about)


async def build_caption(message, rule: ReactionRule, text: str) -> str:
    """Саркастичний підпис у голосі бота, з його персоною й кастомною роллю."""
    user = message.from_user
    user_id = user.id if user else 0
    chat_id = message.chat.id

    instruction = (
        f"У чаті щойно зʼявилось повідомлення про {rule.subject}:\n"
        f"«{text[:1500]}»\n\n"
        "Ти відповідаєш на нього картинкою. Напиши до неї ОДИН короткий "
        "саркастичний коментар, максимум два речення. Коментуй саму новину, "
        "а не того, хто її надіслав."
    )
    response = await _engine.generate_response(
        message=message,
        quoted_block="",
        traits_block=await db_call(get_traits_block, user_id),
        user_name=user.full_name if user else "Невідомий пасажир",
        user_message=instruction,
        custom_role=await db_call(get_custom_role, chat_id),
        is_creator=False,
    )
    response = (response or "").strip()
    if len(response) > CAPTION_LIMIT:
        response = response[: CAPTION_LIMIT - 1].rstrip() + "…"
    return response


async def _send(message, image: Path, caption: str):
    kwargs = {}
    if caption:
        kwargs = {"caption": format_telegram_html(caption), "parse_mode": ParseMode.HTML}

    with image.open("rb") as media:
        if image.suffix.lower() in ANIMATION_EXTENSIONS:
            return await message.reply_animation(animation=media, **kwargs)
        return await message.reply_photo(photo=media, **kwargs)


async def maybe_react(message, text: str, chat_id: int) -> bool:
    """Реагує картинкою, якщо повідомлення стосується однієї з тем.

    Повертає True, якщо картинку надіслано. Не більше однієї реакції на
    повідомлення, навіть якщо збіглось кілька правил.
    """
    if not text or chat_id not in config.PANBOT_CHAT_IDS:
        return False

    try:
        for rule in matching_rules(text, message):
            now = int(time())

            if await db_call(
                is_reaction_on_cooldown, chat_id, rule.name, now, rule.cooldown_seconds
            ):
                continue

            image = pick_image(rule)
            if image is None:
                config.log.warning(
                    f"Reaction '{rule.name}': немає картинок у {rule.images_dir}"
                )
                continue

            # Тег перевіряти не треба: Telegram сам сказав, кого тегнули.
            if rule.needs_confirmation and not await confirm_mention(rule, text, chat_id):
                config.log.info(f"Reaction '{rule.name}': збіг регулярки, але не про тему")
                continue

            # Слот займаємо ПІСЛЯ підтвердження: інакше «кличка собаки»
            # зʼїдала б кулдаун, і на справжню новину бот би промовчав.
            if not await db_call(
                claim_reaction, chat_id, rule.name, now, rule.cooldown_seconds
            ):
                continue

            caption = ""
            if rule.generate_caption:
                # Без підпису картинка все одно доречна, тож збій LLM її не скасовує.
                try:
                    caption = await build_caption(message, rule, text)
                except Exception as e:
                    config.log.exception(f"Reaction '{rule.name}': підпис не вдався: {e}")

            sent = await _send(message, image, caption)
            config.log.info(
                f"Reaction '{rule.name}' sent in chat {chat_id}: {image.name}"
            )

            if caption:
                ts = sent.date if sent.date.tzinfo else sent.date.replace(tzinfo=timezone.utc)
                await db_call(
                    add_message,
                    chat_id,
                    sent.message_id,
                    config.BOT_USER_ID,
                    None,
                    "PanBot",
                    caption,
                    message.message_id,
                    utc_ts(ts.astimezone(timezone.utc)),
                )
            return True
    except Exception as e:
        config.log.exception(f"Reaction failed in chat {chat_id}: {e}")

    return False


def log_reaction_assets() -> None:
    """На старті показує, скільки картинок бачить кожне правило.

    Картинки живуть поза репозиторієм, тож «реакція не спрацьовує, бо тека
    порожня або не той шлях» — найімовірніша поломка. Краще бачити це в
    логах одразу, ніж здогадуватись.
    """
    for rule in _rules():
        directory = rule.images_dir
        if not directory.is_dir():
            config.log.warning(f"Reaction '{rule.name}': теки {directory} немає — реакція вимкнена")
            continue
        count = len(list_media(directory))
        level = config.log.info if count else config.log.warning
        kind = " (тег)" if rule.is_mention else ""
        level(f"Reaction '{rule.name}'{kind}: {count} картинок у {directory}")


def schedule_reaction(context, message, text: str | None, chat_id: int) -> None:
    """Запускає реакцію у фоні, не затримуючи обробку повідомлення.

    PTB обробляє апдейти по одному. Підтвердження й підпис — це кілька
    секунд роботи моделі, і весь цей час бот не відповідав би в жодному чаті.
    Регулярку перевіряємо тут же: фонову задачу на кожне повідомлення
    створювати нема сенсу.
    """
    if not text or chat_id not in config.PANBOT_CHAT_IDS or not matching_rules(text, message):
        return

    coroutine = maybe_react(message, text, chat_id)
    application = getattr(context, "application", None)
    if application is not None:
        application.create_task(coroutine, name=f"reaction:{chat_id}")
        return

    task = asyncio.create_task(coroutine)
    _background.add(task)
    task.add_done_callback(_background.discard)
