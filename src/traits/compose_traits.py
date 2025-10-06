import json
import os
from time import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from openai import AsyncOpenAI

from src.tools import config
from src.tools.db import _get_last_user_messages, upsert_user_traits

KYIV = config.KYIV if hasattr(config, "KYIV") else ZoneInfo("Europe/Kyiv")

MAX_PROMPT_TOKENS = 28000  # запас для моделей з довгим контекстом
MODEL_NAME = os.getenv("TRAITS_LLM_MODEL", "gpt-5")
TRAITS_VERSION = "v1-llm-500"

def _openai_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))

def _messages_to_snippet(rows: list[dict], max_chars_per_line: int = 500) -> str:
    lines: list[str] = []
    for r in rows:
        ts = datetime.fromtimestamp(int(r["ts_utc"]), tz=timezone.utc).astimezone(KYIV)
        t = ts.strftime("%Y-%m-%d %H:%M")
        name = r.get("full_name") or (r.get("username") and f"@{r['username']}") or f"id{r.get('user_id','')}"
        text = (r.get("text") or "").replace("\n", " ").strip()
        if len(text) > max_chars_per_line:
            text = text[:max_chars_per_line] + "…"
        lines.append(f"[{t}] {name}: {text}")
    snippet = "\n".join(lines)
    if len(snippet) > MAX_PROMPT_TOKENS * 4:  # грубе наближення символів->токенів
        snippet = snippet[: MAX_PROMPT_TOKENS * 4]
    return snippet

def _build_traits_prompt(lang: str = "uk") -> str:
    if lang == "uk":
        return (
            "Ти аналітик поведінки користувачів. На основі хронології повідомлень одного користувача "
            "визнач і стисло сформулюй його комунікаційні «traits». "
            "Враховуй теми інтересів, тон (бейзлайн доброзичливості/сарказму/токсичності), "
            "лаконічність чи багатослівність, використання емодзі, приблизні години активності, "
            "мовні вподобання/код-мікс. Враховуй неоднорідність: вкажи впевненість по кожній рисі.\n\n"
            "Формат відповіді — СТРОГО один JSON-об'єкт без зайвого тексту:\n"
            "{\n"
            '  "version": "v1-llm-500",\n'
            '  "summary": "1-2 речення з коротким описом користувача",\n'
            '  "topics": [{"name": "string", "score": 0..1}],\n'
            '  "tone": {"friendliness": 0..1, "sarcasm": 0..1, "toxicity": 0..1, "confidence": 0..1},\n'
            '  "style": {"verbosity": 0..1, "emoji_usage": 0..1, "confidence": 0..1},\n'
            '  "activity": {"hours_utc": [int], "confidence": 0..1},\n'
            '  "language": {"primary": "uk|en|mixed|other", "notes": "string", "confidence": 0..1}\n'
            "}\n"
            "Якщо даних мало, все одно поверни валідний JSON з низькою впевненістю."
        )
    else:
        return (
            "You are a user-behavior analyst. From a single user's message history, "
            "infer concise communication traits: interests/topics, tone (friendliness/sarcasm/toxicity), "
            "verbosity, emoji usage, active hours, and language preferences. Include confidence per aspect.\n\n"
            "Respond STRICTLY as a single JSON object:\n"
            '{ "version":"v1-llm-500", "summary":"...", "topics":[...], "tone":{...}, "style":{...}, "activity":{...}, "language":{...} }'
        )

async def refresh_user_traits_from_messages_llm(user_id: int, lang: str = "uk") -> dict:
    """
    Формує traits через LLM на основі останніх 500 повідомлень користувача і зберігає у user_traits.
    """
    rows = _get_last_user_messages(user_id, limit=500)
    snippet = _messages_to_snippet(rows)

    traits: dict[str, Any] = {
        "version": TRAITS_VERSION,
        "summary": "",
        "topics": [],
        "tone": {"friendliness": 0, "sarcasm": 0, "toxicity": 0, "confidence": 0},
        "style": {"verbosity": 0, "emoji_usage": 0, "confidence": 0},
        "activity": {"hours_utc": [], "confidence": 0},
        "language": {"primary": "mixed", "notes": "", "confidence": 0},
        "sample_size": len(rows),
        "updated_from": "llm_messages_last_500",
    }

    if not _openai_enabled() or not rows:
        # fallback — збережемо «пусті» трейти з нульовою впевненістю
        upsert_user_traits(user_id, traits, int(time()))
        return traits

    prompt = _build_traits_prompt(lang=lang)

    async with AsyncOpenAI() as client:
        resp = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "Ти формуєш компактний, структурований профіль traits на основі історії повідомлень одного користувача. Повертай тільки валідний JSON."},
                {"role": "user", "content": f"{prompt}\n\nОсь останні повідомлення користувача (від нових до старих):\n{snippet}"},
            ],
            response_format={"type": "json_object"},
        )

    try:
        content = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(content)
        parsed["version"] = TRAITS_VERSION
        parsed["sample_size"] = len(rows)
        parsed["updated_from"] = "llm_messages_last_500"
        upsert_user_traits(user_id, parsed, int(time()))
        return parsed
    except Exception as e:
        config.log.exception(f"Traits JSON parsing failed: {e}")
        upsert_user_traits(user_id, traits, int(time()))
        return traits