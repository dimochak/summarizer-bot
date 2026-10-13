"""Відповідь на голосові й кружечки: токсичний вступ + розшифровка.

Потік:
    тривалість? -> денний ліміт -> завантаження -> Gemini -> запис в історію
    -> відповідь за шаблоном (speech / snoring / other)

Розшифровка лягає в messages.text, тож голосові потрапляють і в щоденні
підсумки, і в контекст PanBot без жодних змін у них.
"""
import asyncio
import html
import re
from datetime import datetime, timezone

from telegram.constants import ChatAction, ParseMode

import src.tools.config as config
from src.tools.db import add_message, claim_voice_slot, db_call, release_voice_slot
from src.tools.utils import utc_ts
from src.voice.analyze import VoiceAnalysis, analyze_voice
from src.voice.render import render, uk_duration

# Telegram обмежує повідомлення 4096 символами після розбору HTML.
# Запас — на вступ і підпис над розшифровкою.
TRANSCRIPT_LIMIT = 3500

# Gemini приймає вбудовані дані до 20 МБ на весь запит, а base64 роздуває
# файл на третину. Голосові такого розміру не бувають — це запобіжник.
MAX_INLINE_BYTES = 14 * 1024 * 1024

LABELS = {"voice": ("🎤", "голосове"), "video_note": ("📹", "кружечок")}
TEMPLATES = {"speech": "speech.j2", "snoring": "snoring.j2", "other": "noise.j2"}

_TAG = re.compile(r"<[^>]+>")

# Сильні посилання на фонові задачі: asyncio тримає лише слабкі.
_background: set[asyncio.Task] = set()


def media_of(message):
    """(вид, обʼєкт медіа, mime) або None, якщо це не голосове й не кружечок."""
    if getattr(message, "voice", None):
        return "voice", message.voice, message.voice.mime_type or "audio/ogg"
    if getattr(message, "video_note", None):
        # VideoNote не має mime_type: Telegram завжди віддає MP4.
        return "video_note", message.video_note, "video/mp4"
    return None


def truncate(transcript: str) -> tuple[str, bool]:
    if len(transcript) <= TRANSCRIPT_LIMIT:
        return transcript, False
    return transcript[:TRANSCRIPT_LIMIT].rstrip() + "…", True


def _utc(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return utc_ts(dt.astimezone(timezone.utc))


async def _remember(message, text: str) -> None:
    """Пише саме голосове в історію чату — з розшифровкою або міткою."""
    user = message.from_user
    await db_call(
        add_message,
        message.chat.id,
        message.message_id,
        user.id if user else None,
        user.username if user else None,
        user.full_name if user else None,
        text,
        message.reply_to_message.message_id if message.reply_to_message else None,
        _utc(message.date),
    )


async def _reply(message, text: str, history_text: str | None = None) -> None:
    """Відповідає і пише відповідь бота в історію.

    В історію йде лише вступ, без розшифровки: вона вже лежить у самому
    голосовому, і підсумок дня побачив би її двічі.
    """
    sent = await message.reply_text(text, parse_mode=ParseMode.HTML)
    plain = html.unescape(_TAG.sub("", history_text if history_text is not None else text))
    await db_call(
        add_message,
        message.chat.id,
        sent.message_id,
        config.BOT_USER_ID,
        None,
        "PanBot",
        plain,
        message.message_id,
        _utc(sent.date),
    )


async def _transcribe(message, media, mime_type: str, media_kind: str) -> VoiceAnalysis:
    try:
        thread_id = message.message_thread_id if getattr(message, "is_topic_message", False) else None
        await message.chat.send_action(ChatAction.TYPING, message_thread_id=thread_id)
    except Exception as e:
        # «Друкує…» — прикраса; без неї відповідь усе одно прийде.
        config.log.warning(f"Voice: could not send chat action: {e}")

    file = await media.get_file()
    data = bytes(await file.download_as_bytearray())
    return await analyze_voice(data, mime_type, media_kind, chat_id=message.chat.id)


async def handle_voice(message, is_creator: bool = False) -> None:
    found = media_of(message)
    if found is None:
        return
    media_kind, media, mime_type = found

    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id if user else 0
    icon, label = LABELS[media_kind]
    duration = media.duration or 0
    context = {
        "media": media_kind,
        "user_name": user.full_name if user else "Невідомий пасажир",
        "duration": duration,
        "forwarded": getattr(message, "forward_origin", None) is not None,
        "is_creator": is_creator,
        "limit": config.VOICE_DAILY_LIMIT,
    }

    try:
        # Задовге не завантажуємо взагалі й ліміт на нього не витрачаємо.
        if duration > config.VOICE_MAX_SECONDS or (media.file_size or 0) > MAX_INLINE_BYTES:
            await _remember(message, f"[{icon} {label}, {uk_duration(duration)}, не розшифровано]")
            await _reply(message, render("too_long.j2", **context))
            return

        today = datetime.now(tz=config.KYIV).date().isoformat()
        used = await db_call(claim_voice_slot, user_id, chat_id, today)
        if used > config.VOICE_DAILY_LIMIT:
            await _remember(message, f"[{icon} {label}]")
            # Попереджаємо один раз, на першому зайвому. Далі мовчимо, інакше
            # бот відповідав би відмовою на кожне голосове.
            if used == config.VOICE_DAILY_LIMIT + 1:
                await _reply(message, render("limit.j2", **context))
            return

        try:
            analysis = await _transcribe(message, media, mime_type, media_kind)
        except Exception as e:
            config.log.exception(f"Voice analysis failed in chat {chat_id}: {e}")
            await db_call(release_voice_slot, user_id, chat_id, today)
            await _remember(message, f"[{icon} {label}]")
            await _reply(message, render("failed.j2", **context))
            return

        config.log.info(
            f"Voice in chat {chat_id}: {media_kind}, {duration}s -> {analysis.kind}, "
            f"{len(analysis.transcript)} chars"
        )

        if analysis.kind == "speech":
            await _remember(message, f"{icon} {analysis.transcript}")
        elif analysis.kind == "snoring":
            await _remember(message, f"[{icon} {label}: хропіння]")
        else:
            await _remember(message, f"[{icon} {label} без слів]")

        intro = render(TEMPLATES[analysis.kind], **context)
        if analysis.kind != "speech":
            await _reply(message, intro)
            return

        transcript, truncated = truncate(analysis.transcript)
        body = render("transcript.j2", transcript=transcript, truncated=truncated, **context)
        await _reply(message, f"{intro}\n\n{body}", history_text=intro)
    except Exception as e:
        config.log.exception(f"Voice reply failed in chat {chat_id}: {e}")


def schedule_voice(context, message, is_creator: bool = False) -> None:
    """Запускає обробку у фоні, не затримуючи інші апдейти.

    PTB обробляє апдейти по одному, а завантаження й Gemini — це кілька
    секунд, весь цей час бот мовчав би в усіх чатах.
    """
    coroutine = handle_voice(message, is_creator=is_creator)
    application = getattr(context, "application", None)
    if application is not None:
        application.create_task(coroutine, name=f"voice:{message.chat.id}")
        return

    task = asyncio.create_task(coroutine)
    _background.add(task)
    task.add_done_callback(_background.discard)
