import asyncio
from time import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.core.llm import get_structured_llm
from src.tools import config
from src.tools.db import (
    _get_last_user_messages,
    db_call,
    get_user_ids_with_stale_traits,
    upsert_user_traits,
)


class TraitTopic(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)


class TraitTone(BaseModel):
    friendliness: float = Field(ge=0.0, le=1.0)
    sarcasm: float = Field(ge=0.0, le=1.0)
    toxicity: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class TraitStyle(BaseModel):
    verbosity: float = Field(ge=0.0, le=1.0)
    emoji_usage: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class TraitActivity(BaseModel):
    hours_utc: list[int] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class TraitLanguage(BaseModel):
    primary: str
    notes: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class UserTraits(BaseModel):
    """Схема профілю користувача.

    Раніше опис формату жив лише текстом у промпті, а відповідь розбиралась
    ручним json.loads — без жодної валідації діапазонів.
    """

    summary: str
    topics: list[TraitTopic] = Field(default_factory=list)
    tone: TraitTone
    style: TraitStyle
    activity: TraitActivity
    language: TraitLanguage

KYIV = config.KYIV if hasattr(config, "KYIV") else ZoneInfo("Europe/Kyiv")

MAX_PROMPT_TOKENS = 28000  # запас для моделей з довгим контекстом
TRAITS_VERSION = "v1-llm-500"

# Модель тепер задається в config.TRAITS_MODEL_NAME (та сама змінна оточення
# TRAITS_LLM_MODEL) і резолвиться фабрикою через purpose="traits".


def _openai_enabled() -> bool:
    return bool(config.OPENAI_API_KEY)

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
    rows = await db_call(_get_last_user_messages, user_id, 500)
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
        await db_call(upsert_user_traits, user_id, traits, int(time()))
        return traits

    prompt = _build_traits_prompt(lang=lang)

    llm = get_structured_llm(UserTraits, purpose="traits")
    messages = [
        SystemMessage(
            content="Ти формуєш компактний, структурований профіль traits на основі "
                    "історії повідомлень одного користувача."
        ),
        HumanMessage(
            content=f"{prompt}\n\nОсь останні повідомлення користувача "
                    f"(від нових до старих):\n{snippet}"
        ),
    ]

    try:
        result = await llm.ainvoke(messages)
        parsed = result.model_dump()
        parsed["version"] = TRAITS_VERSION
        parsed["sample_size"] = len(rows)
        parsed["updated_from"] = "llm_messages_last_500"
        await db_call(upsert_user_traits, user_id, parsed, int(time()))
        return parsed
    except Exception as e:
        config.log.exception(f"Traits generation failed: {e}")
        await db_call(upsert_user_traits, user_id, traits, int(time()))
        return traits

async def refresh_stale_user_traits(
    lang: str = "uk",
    now_ts: int | None = None,
) -> int:
    """Оновлює профілі, які застаріли (за замовчуванням старші за 30 днів).

    Раніше traits оновлювались ЛИШЕ ручним запуском scripts-подібного
    backfill_traits.py, тож get_traits_block читав те, що колись залишив
    разовий прогін. Тепер це робить щоденний джоб, беручи за раз обмежену
    пачку користувачів.

    Повертає кількість успішно оновлених профілів.
    """
    if not _openai_enabled():
        config.log.warning("Traits refresh skipped: OPENAI_API_KEY не заданий")
        return 0

    now_ts = now_ts if now_ts is not None else int(time())
    cutoff = now_ts - config.TRAITS_REFRESH_DAYS * 86400

    user_ids = await db_call(
        get_user_ids_with_stale_traits, cutoff, config.TRAITS_REFRESH_BATCH
    )
    if not user_ids:
        config.log.info("Traits refresh: усі профілі свіжі")
        return 0

    sem = asyncio.Semaphore(config.TRAITS_REFRESH_CONCURRENCY)
    succeeded = 0

    async def worker(uid: int) -> None:
        nonlocal succeeded
        async with sem:
            try:
                await refresh_user_traits_from_messages_llm(uid, lang=lang)
                succeeded += 1
            except Exception as e:
                config.log.exception(f"Traits refresh failed for user_id={uid}: {e}")

    await asyncio.gather(*(worker(uid) for uid in user_ids))
    config.log.info(f"Traits refresh: оновлено {succeeded} з {len(user_ids)} профілів")
    return succeeded
