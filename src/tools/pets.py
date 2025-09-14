import base64
import io
import os

from telegram import Update
from telegram.ext import ContextTypes
from openai import AsyncOpenAI
import json

from src.tools import config

# Configuration
PET_CONFIDENCE_THRESHOLD = float(os.getenv("PET_CONFIDENCE_THRESHOLD", "0.6"))

# Simple provider switch: prefer OpenAI if key present
def _openai_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))

async def _download_photo_bytes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bytes, str | None]:
    """
    Returns (bytes, file_id) for the largest photo in the message.
    """
    if not update.message or not update.message.photo:
        return b"", None
    size = update.message.photo[-1]
    file = await context.bot.get_file(size.file_id)
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)
    return bio.getvalue(), size.file_id

# New: download by file_id (used in on-demand detection)
async def _download_file_bytes(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes:
    file = await context.bot.get_file(file_id)
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)
    return bio.getvalue()


def _build_joint_prompt(sarcasm_level: int = 5, lang: str = "uk") -> str:
    """
    Joint prompt for pet detection + caption, single JSON response.
    Sarcasm scale: 0 (no sarcasm) ... 9 (toxic trolling). We aim for ~5 by default.
    """
    # Keep caption short, single sentence, slightly sarcastic.
    tone = (
        f"Ступінь сарказму: {max(0, min(9, sarcasm_level))} з 9. "
        "Будь дотепним без токсичності чи образ. Одне коротке речення, без емодзі, без форматування."
        if lang == "uk"
        else f"Sarcasm level: {max(0, min(9, sarcasm_level))}/9. Short, witty, non-toxic, one sentence, no emojis/formatting."
    )
    instr_uk = (
        "Твоє завдання: подивитися на зображення і визначити, чи є там кіт або пес. "
        "Якщо ні — species='none'. Потім дай короткий дотепний підпис українською (1 речення),"
        " з легкою іронією (приблизно на рівні 5 з 9), без емодзі, без форматування, без згадок про ШІ чи моделі."
    )
    output_spec = (
        "Відповідай СТРОГО у форматі JSON:\n"
        '{\n'
        '  "species": "cat" | "dog" | "none",\n'
        '  "confidence": number (0..1),\n'
        '  "caption": "one short sentence in Ukrainian"\n'
        '}\n'
        "Без додаткового тексту поза JSON."
    )
    return f"{instr_uk}\n\n{tone}\n\n{output_spec}"


def _parse_joint_json(text: str) -> tuple[str, float, str]:
    try:
        data = json.loads(text.strip())
        species = str(data.get("species", "none")).lower()
        if species not in ("cat", "dog", "none"):
            species = "none"
        conf = float(data.get("confidence", 0.0))
        if conf < 0 or conf > 1:
            conf = 0.0
        caption = str(data.get("caption") or "").strip()
        return species, conf, caption
    except Exception as e:
        config.log.exception(f"JSON parsing failed: {e}")
        return "none", 0.0, ""


async def detect_and_caption_with_openai(image_bytes: bytes, sarcasm_level: int = 5) -> tuple[str, float, str]:
    """
    Single-call OpenAI flow: returns (species, confidence, caption).
    - species: 'cat' | 'dog' | 'none'
    - confidence: 0..1
    - caption: short Ukrainian sentence, sarcasm ~ level 5
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"
    model = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")
    prompt = _build_joint_prompt(sarcasm_level=sarcasm_level, lang="uk")

    async with AsyncOpenAI() as client:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            response_format={"type": "json_object"},
        )

    text = (resp.choices[0].message.content or "").strip()
    return _parse_joint_json(text)


async def detect_and_caption(image_bytes: bytes, sarcasm_level: int = 5) -> tuple[str, float, str]:
    """
    Wrapper to do pet detection + caption in one go if OpenAI is available.
    Fallback: returns no detection + a generic slightly sarcastic caption.
    """
    if _openai_enabled():
        return await detect_and_caption_with_openai(image_bytes, sarcasm_level=sarcasm_level)
    generic_caption = "Фото ніби натякає, що люди тут раби для тварин."
    return "none", 0.0, generic_caption