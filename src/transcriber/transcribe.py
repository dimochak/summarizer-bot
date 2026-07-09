import google.generativeai as genai
from telegram.ext import ContextTypes

import src.tools.config as config

genai.configure(api_key=config.GEMINI_API_KEY)
# Plain-text output (no JSON mime type) for verbatim transcripts.
_transcriber_model = genai.GenerativeModel(config.GEMINI_MODEL_NAME)

_TRANSCRIBE_PROMPT = (
    "Розшифруй мовлення з цього запису у текст. "
    "Поверни ЛИШЕ дослівний транскрипт мовою оригіналу, "
    "без пояснень, коментарів, підписів чи форматування. "
    "Якщо у записі немає розбірливого мовлення — поверни порожній рядок."
)


async def transcribe_by_file_id(
    context: ContextTypes.DEFAULT_TYPE, file_id: str, mime_type: str
) -> str:
    """
    Download a Telegram audio/video file by file_id and transcribe it to text via Gemini.

    Gemini accepts the media inline (bytes + mime_type), which is fine for the small
    voice messages / video notes Telegram allows the bot to download (≤20MB). Returns
    the verbatim transcript, or an empty string if no speech was recognized.
    """
    try:
        tg_file = await context.bot.get_file(file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as e:
        config.log.exception(f"Failed to download file {file_id} for transcription: {e}")
        raise

    config.log.info(f"Transcribing {file_id} ({mime_type}, {len(data)} bytes)")
    resp = _transcriber_model.generate_content(
        [_TRANSCRIBE_PROMPT, {"mime_type": mime_type, "data": data}]
    )
    return (resp.text or "").strip()
