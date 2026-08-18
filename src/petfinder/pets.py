import os
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from src.core.llm import get_structured_llm
from src.tools import config

# Configuration
PET_CONFIDENCE_THRESHOLD = float(os.getenv("PET_CONFIDENCE_THRESHOLD", "0.6"))
SARCASM_LEVEL = 7


class PetDetection(BaseModel):
    species: Literal["cat", "dog", "none"] = Field(description="Хто на фото")
    confidence: float = Field(ge=0.0, le=1.0, description="Впевненість від 0 до 1")
    caption: str = Field(description="Одне коротке іронічне речення українською")


def _openai_enabled() -> bool:
    return bool(config.OPENAI_API_KEY)


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
    # Опис формату не потрібен: схему PetDetection нав'язує сам structured output,
    # а раніше тут дублювався ще й ручний парсер JSON.
    return f"{instr_uk}\n\n{tone}"


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

    prompt = _build_joint_prompt(sarcasm_level=sarcasm_level, lang="uk")

    llm = get_structured_llm(PetDetection, purpose="vision")
    messages = [
        SystemMessage(
            content="Ти іронічний помічник, який допомагає знаходити фото котів або собак в чаті."
        ),
        HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        ),
    ]

    try:
        result = await llm.ainvoke(messages)
    except Exception as e:
        config.log.exception(f"Pet detection failed: {e}")
        return "none", 0.0, ""

    return result.species, result.confidence, result.caption.strip()


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