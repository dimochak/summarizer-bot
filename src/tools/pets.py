import base64
import io
import os

from telegram import Update
from telegram.ext import ContextTypes


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

def _build_detection_prompt() -> str:
    return (
        "You are an image inspector. Task:\n"
        "- Look at the photo and decide if it contains a cat or a dog.\n"
        "- If neither is clearly visible, answer 'species: none'.\n"
        "Respond strictly as JSON with keys: species ('cat'|'dog'|'none'), confidence (0..1).\n"
        'Example: {"species":"cat","confidence":0.92}'
    )

def _parse_species_json(text: str) -> tuple[str, float]:
    import json
    try:
        data = json.loads(text.strip())
        species = str(data.get("species", "none")).lower()
        conf = float(data.get("confidence", 0.0))
        if species not in ("cat", "dog", "none"):
            species = "none"
        if conf < 0 or conf > 1:
            conf = 0.0
        return species, conf
    except Exception:
        return "none", 0.0

async def _detect_with_openai(image_bytes: bytes) -> tuple[str, float]:
    from openai import OpenAI
    client = OpenAI()

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"

    prompt = _build_detection_prompt()
    model = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")

    resp = client.chat.completions.create(
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
        temperature=0,
    )
    text = resp.choices[0].message.content or ""
    return _parse_species_json(text)

async def detect_pet_species(image_bytes: bytes) -> tuple[str, float]:
    if _openai_enabled():
        return await _detect_with_openai(image_bytes)
    return "none", 0.0