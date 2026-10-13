"""Ручна перевірка розпізнавання на справжніх записах. Не для CI.

Юніт-тести не відповідають на головне питання: чи модель справді відрізняє
хропіння від мовлення й шуму. Цей скрипт ганяє analyze_voice по теці з
записами через справжній Gemini (потрібен GEMINI_API_KEY; решту змінних, які
вимагає config, можна задати будь-якими).

    uv run python -m src.voice.eval samples/
    VOICE_GEMINI_MODEL_NAME=gemini-2.5-flash-lite uv run python -m src.voice.eval samples/

У проді ключі вже є: записи кладемо на том і запускаємо там же:

    fly ssh console -C "uv run python -m src.voice.eval /app/data/voice-samples"

Голосові — .ogg/.oga/.opus/.mp3/.wav, кружечки — .mp4. Якщо імʼя файлу
починається з очікуваного виду (speech_, snoring_, other_), скрипт порахує
збіги. Найгірша помилка — мовлення, прийняте за хропіння: людина щось
сказала, а бот велить їй прокидатись.
"""
import asyncio
import sys
from pathlib import Path

import src.tools.config as config
from src.voice.analyze import analyze_voice

FORMATS = {
    ".ogg": ("audio/ogg", "voice"),
    ".oga": ("audio/ogg", "voice"),
    ".opus": ("audio/ogg", "voice"),
    ".mp3": ("audio/mp3", "voice"),
    ".wav": ("audio/wav", "voice"),
    ".mp4": ("video/mp4", "video_note"),
}
KINDS = {"speech", "snoring", "other"}


async def main(folder: Path) -> int:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in FORMATS)
    if not files:
        print(f"У {folder} немає записів ({', '.join(FORMATS)})")
        return 1

    model = config.VOICE_GEMINI_MODEL_NAME or config.GEMINI_MODEL_NAME
    print(f"Модель: {model}, файлів: {len(files)}\n")

    labelled = matched = 0
    speech_as_snoring = []
    for path in files:
        mime_type, media = FORMATS[path.suffix.lower()]
        expected = path.stem.split("_")[0]
        expected = expected if expected in KINDS else None
        try:
            result = await analyze_voice(path.read_bytes(), mime_type, media)
        except Exception as e:
            print(f"!! {path.name}: {e}")
            continue

        mark = "  "
        if expected:
            labelled += 1
            matched += result.kind == expected
            mark = "ok" if result.kind == expected else "XX"
        if expected == "speech" and result.kind == "snoring":
            speech_as_snoring.append(path.name)
        print(f"{mark} {path.name}: {result.kind} | {result.transcript[:300]}")

    if labelled:
        print(f"\nЗбіглося {matched} з {labelled}")
    if speech_as_snoring:
        print(f"Мовлення прийняте за хропіння: {', '.join(speech_as_snoring)}")
        return 2
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
