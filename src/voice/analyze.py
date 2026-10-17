"""Розбір голосового чи кружечка: що там звучить і що сказано.

Один виклик Gemini замість пари «Whisper + класифікатор». Whisper уміє лише
мовлення: на хропінні чи тиші він або мовчить, або вигадує фрази на кшталт
«Дякую за перегляд». Gemini чує й немовні звуки, тож одразу каже, що це, і
приймає OGG/MP4 з Telegram без перекодування.
"""
import base64
from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from src.core.llm import get_structured_llm
from src.voice.render import render

MediaKind = Literal["voice", "video_note"]


class VoiceAnalysis(BaseModel):
    kind: Literal["speech", "snoring", "other"] = Field(
        description=(
            "speech — є розбірливе мовлення, навіть якщо на фоні хропуть чи шумить; "
            "snoring — лише хропіння або сонне сопіння, жодного слова; "
            "other — тиша, музика, шум, нерозбірливе бурмотіння"
        )
    )
    transcript: str = Field(
        default="",
        description="Дослівна розшифровка мовою оригіналу; порожньо, якщо kind не speech",
    )


def build_message(data: bytes, mime_type: str, media: MediaKind) -> HumanMessage:
    return HumanMessage(content=[
        {"type": "text", "text": render("analyze.j2", media=media)},
        {
            "type": "video" if media == "video_note" else "audio",
            "base64": base64.b64encode(data).decode("ascii"),
            "mime_type": mime_type,
        },
    ])


async def analyze_voice(
    data: bytes, mime_type: str, media: MediaKind, chat_id: int | None = None
) -> VoiceAnalysis:
    llm = get_structured_llm(VoiceAnalysis, chat_id=chat_id, purpose="voice")
    result = await llm.ainvoke([build_message(data, mime_type, media)])

    # Класифікації віримо більше, ніж тексту: модель могла «розшифрувати»
    # хропіння, а мовлення без жодного слова — це не мовлення.
    transcript = (result.transcript or "").strip()
    if result.kind != "speech":
        return VoiceAnalysis(kind=result.kind)
    if not transcript:
        return VoiceAnalysis(kind="other")
    return VoiceAnalysis(kind="speech", transcript=transcript)
