import os

from telegram.ext import ContextTypes
from openai import AsyncOpenAI
import json

from src.tools import config

# Configuration
PET_CONFIDENCE_THRESHOLD = float(os.getenv("PET_CONFIDENCE_THRESHOLD", "0.6"))
SARCASM_LEVEL = 7

def _openai_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _build_joint_prompt(sarcasm_level: int = SARCASM_LEVEL, lang: str = "uk") -> str:
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
        "Якщо ні — species='none'. Потім дай короткий іронічний підпис українською (1 речення),"
        " без емодзі, без форматування, без згадок про ШІ чи моделі."
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


async def detect_and_caption_from_url(image_url: str, sarcasm_level: int = SARCASM_LEVEL) -> tuple[str, float, str]:
    """
    Detects and generates a sarcastic caption from the given image URL.

    The function processes an input image URL to generate a sarcastic caption using
    an AI language model. The level of sarcasm can be controlled via the
    `sarcasm_level` parameter. If the AI backend is disabled, it will return a generic
    caption as a fallback. The result is returned as a tuple containing the generated
    caption type, a confidence score, and the actual caption.

    :param image_url: The URL of the image to be processed.
    :param sarcasm_level: An integer representing the level of sarcasm in the
        generated caption. Default is 5.
    :return: A tuple consisting of:
        - The type of caption as a string (e.g., "sarcastic").
        - The confidence score as a float between 0 and 1.
        - The generated sarcastic caption as a string.
    """
    if not _openai_enabled():
        generic_caption = "Фото ніби натякає, що люди тут раби для тварин."
        return "none", 0.0, generic_caption

    model = "gpt-5-nano"
    prompt = _build_joint_prompt(sarcasm_level=sarcasm_level, lang="uk")

    async with AsyncOpenAI() as client:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Ти іронічний помічник, який допомагає знаходити фото котів або собак в чаті. Завжди відповідай у форматі JSON"
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            response_format={"type": "json_object"},
        )

    text = (resp.choices[0].message.content or "").strip()
    return _parse_joint_json(text)


async def detect_and_caption_by_file_id(context: ContextTypes.DEFAULT_TYPE, file_id: str,
                                        sarcasm_level: int = SARCASM_LEVEL) -> tuple[str, float, str]:
    """
    Resolves Telegram file_id to a direct file URL and runs detection via URL (no base64 inlining).
    """
    try:
        file = await context.bot.get_file(file_id)
        image_url = file.file_path
    except Exception as e:
        config.log.exception(f"Failed to resolve file_id to URL: {e}")
        return "none", 0.0, ""

    return await detect_and_caption_from_url(image_url, sarcasm_level=sarcasm_level)