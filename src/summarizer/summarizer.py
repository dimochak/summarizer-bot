from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape

import tiktoken
from pydantic import BaseModel, Field

from telegram import Chat
from telegram.ext import ContextTypes

import src.tools.config as config
from src.core.llm import get_structured_llm, resolve_provider
from src.tools.db import db_call, get_messages_between
from src.tools.utils import utc_ts, clean_text, message_link, user_link

MAX_TOPICS_NUM = 7


class Topic(BaseModel):
    short_title: str = Field(description="≤7 слів, змістовна назва теми")
    first_message_id: int = Field(description="message_id першого повідомлення теми")
    initiator_user_id: int = Field(description="user_id автора першого повідомлення теми")
    summary: str = Field(description="1–2 речення підсумку у відповідному стилі")


class DaySummary(BaseModel):
    topics: list[Topic] = Field(default_factory=list)


class ChatSummary(BaseModel):
    """Вільний підсумок довільного відрізка чату (запит «підсумуй останні N…»)."""

    summary: str = Field(description="Короткий іронічний підсумок українською")


def get_toxicity_prompt(toxicity_level: int) -> str:
    """Generate prompt based on toxicity level (0-9)"""

    base_prompt = f"""Ти — помічник, що групує повідомлення чату у теми за календарний день.

Завдання:
1) Зкластеризуй повідомлення у 2–{MAX_TOPICS_NUM} тем.
2) Для кожної теми визнач:
   - short_title: ≤7 слів, змістовна назва
   - first_message_id: message_id першого (найранішого) повідомлення в темі
   - initiator_user_id: user_id автора першого повідомлення теми
   - summary: 1–2 речення підсумку з коментарем у відповідному стилі. 
Якщо одна з тем стосується зборів на українську армію -- заохоть людей задонатити на збір, але тільки у відповідній темі. 

3) Поверни РІВНО JSON такого вигляду:
{{
  "topics": [
    {{
      "short_title": "…",
      "first_message_id": 123,
      "initiator_user_id": 456,
      "summary": "…"
    }}
  ]
}}

УВАГА:
- Орієнтуйся на reply-ланцюжки як ознаку теми; для нереплайних — об'єднуй за змістом.
- Ігноруй службові повідомлення/стікери, якщо вони нічого не додають по суті.
"""

    toxicity_styles = {
        0: """
СТИЛЬОВІ ВИМОГИ:
- Використовуй дружелюбний та позитивний тон
- Будь підтримуючим та конструктивним
- Знаходь позитивні моменти в обговореннях
- Для російських тем - нейтральний опис без емоцій
- Уникай будь-якої критики або негативу
""",
        1: """
СТИЛЬОВІ ВИМОГИ:
- Легкий гумор без образ
- М'який та дружелюбний тон
- Невеликі жартівливі коментарі
- Для російських тем - стриманий опис
- Позитивний настрій
""",
        2: """
СТИЛЬОВІ ВИМОГИ:
- Помірний гумор та іронія
- Легка критика без агресії
- Жартівливі зауваження
- Для російських тем - легка критика
- Дружелюбна атмосфера
""",
        3: """
СТИЛЬОВІ ВИМОГИ:
- Іронія та сарказм у помірних дозах
- М'яка критика дурниць
- Жартівливе висміювання
- Для російських тем - критичні зауваження
- Баланс між гумором та серйозністю
""",
        4: """
СТИЛЬОВІ ВИМОГИ:
- Помітний сарказм та іронія
- Критика глупоти без жорстокості
- Досить їдкі коментарі
- Для російських тем - негативне ставлення
- Трохи провокаційний стиль
""",
        5: """
СТИЛЬОВІ ВИМОГИ:
- Виражений сарказм та їдкість
- Відверта критика дурниць
- Чорний гумор у помірних дозах
- Для російських тем - різка критика окупантів
- Провокаційні коментарі
""",
        6: """
СТИЛЬОВІ ВИМОГИ:
- Їдкий сарказм та іронія
- Безжалісна критика глупоти
- Чорний гумор
- Для російських тем - жорстка критика рашистів
- Контроверсійні висловлювання
""",
        7: """
СТИЛЬОВІ ВИМОГИ:
- Максимальний сарказм та їдкість
- Нещадна критика тупості та лицемірства
- Активний чорний гумор
- Для російських тем - словесне знищення окупантів
- Дуже провокаційний стиль
""",
        8: """
СТИЛЬОВІ ВИМОГИ:
- Вкрай токсичний сарказм
- Безжалісне висміювання всього безглуздого
- Жорсткий чорний гумор та іронія
- Для російських тем - повне словесне розчавлювання рашистів
- Максимально провокаційні коментарі
""",
        9: """
СТИЛЬОВІ ВИМОГИ:
- Використовуй максимально токсичну лексику та їдкий сарказм
- Безжалісно висміюй тупість, лицемірство, безглуздя
- Для російських тем - повна словесна деструкція окупантів
- Заохочується чорний гумор
- Ніякої толерантності до дурниць
- Будь максимально провокаційним та контроверсійним
""",
    }

    # Clamp toxicity level to 0-9 range
    toxicity_level = max(0, min(9, toxicity_level))

    return base_prompt + toxicity_styles[toxicity_level]


try:
    _encoder = tiktoken.encoding_for_model(config.OPENAI_MODEL_NAME)
except KeyError:
    _encoder = tiktoken.get_encoding("cl100k_base")


def build_messages_snippet(
    rows, max_tokens: int = 30_000, toxicity_level: int = 9
) -> str:
    """Build messages snippet with token limit using tiktoken"""
    lines = []
    current_tokens = 0
    tokens_remaining = max_tokens - len(
        _encoder.encode(get_toxicity_prompt(toxicity_level))
    )

    for r in rows:
        ts = datetime.fromtimestamp(r["ts_utc"], tz=ZoneInfo("UTC")).astimezone(
            config.KYIV
        )
        time = ts.strftime("%H:%M")
        name = (
            r.get("full_name")
            or (r.get("username") and f"@{r['username']}")
            or f"id{r.get('user_id', 'unknown')}"
        )
        frag = (r.get("text") or "").replace("\n", " ").strip()
        if len(frag) > 500:
            frag = frag[:500] + "…"
        
        reply_id = r.get("reply_to_message_id")
        reply = f", reply_to={reply_id}" if reply_id else ""

        line = f"[{time}] {name} (uid={r.get('user_id', 'unknown')}, mid={r.get('message_id', 'unknown')}{reply}): {frag}"

        line_tokens = len(_encoder.encode(line))
        if current_tokens + line_tokens > tokens_remaining:
            break

        current_tokens += line_tokens
        lines.append(line)

    return "\n".join(lines)


async def request_summary(prompt: str, chat_id: int, schema):
    """Один шлях до моделі для будь-якого підсумку.

    Раніше тут було дві майже однакові функції на сирих SDK, кожна зі своїм
    парсингом JSON — у Gemini-версії JSON виколупувався регуляркою з тексту.
    """
    llm = get_structured_llm(schema, chat_id=chat_id, purpose="summary")
    return await llm.ainvoke(prompt)


def _looks_like_safety_block(error: Exception) -> bool:
    """Чи схожа помилка на спрацювання фільтра безпеки, а не на збій конфігурації.

    Від цього залежить лише текст фінального повідомлення користувачу:
    драбинка токсичності однаково пробує знизити рівень при будь-якій помилці.
    """
    text = str(error).lower()
    markers = (
        "safety", "blocked", "block_reason", "content_filter", "content filter",
        "finish_reason", "valid `part`", "recitation", "prohibited",
    )
    return any(marker in text for marker in markers)


def is_chat_configured(chat_id: int) -> bool:
    """Check if chat is configured for any AI provider"""
    return chat_id in config.ALLOWED_CHAT_IDS


async def summarize_day(
    chat: Chat,
    start_local: datetime,
    end_local: datetime,
    ctx: ContextTypes.DEFAULT_TYPE,
    toxicity_level: int = 9,
) -> str | None:
    # Check if chat is configured for any AI provider
    if not is_chat_configured(chat.id):
        config.log.warning(f"Chat {chat.id} is not configured for any AI provider")
        return None

    start_utc = start_local.astimezone(ZoneInfo("UTC"))
    end_utc = end_local.astimezone(ZoneInfo("UTC"))
    rows = await db_call(
        get_messages_between, chat.id, utc_ts(start_utc), utc_ts(end_utc)
    )
    rows = [r for r in rows if clean_text(r["text"])]
    if not rows:
        return None

    snippet = build_messages_snippet(rows)
    day_str = (start_local.date()).strftime("%d.%m.%Y")

    provider_name = resolve_provider(chat.id, "summary")
    config.log.info(f"Using {provider_name} for chat {chat.id}")

    # Try from requested toxicity_level down to 0 until we get a response (fallback on safety blocks)
    requested_level = max(0, min(9, toxicity_level))
    topics = []
    safety_blocked_encountered = False

    for level in range(requested_level, -1, -1):
        prompt = f"""{get_toxicity_prompt(level)}

Нижче повідомлення за день у форматі рядків:
{snippet}
"""
        try:
            config.log.info(
                f"Current toxicity level: {level} (requested: {requested_level})"
            )
            config.log.info(f"Current number of tokens: {len(_encoder.encode(prompt))}")

            result = await request_summary(prompt, chat.id, DaySummary)
            topics = [t.model_dump() for t in result.topics]
            if topics:
                toxicity_level = level  # record the actual level that worked
                break
            # If no topics returned, try a lower toxicity just in case model was overly strict
            config.log.warning(
                f"{provider_name} returned no topics at toxicity level {level}, trying lower level..."
            )
        except Exception as e:
            # Пробуємо нижчий рівень при БУДЬ-ЯКІЙ помилці: раніше умова спиралась на
            # текст винятку конкретного SDK, і після переходу на LangChain такі рядки
            # все одно перестали б збігатися. Тип помилки впливає лише на те, яке
            # повідомлення побачить користувач, якщо драбинка вичерпається.
            if _looks_like_safety_block(e):
                safety_blocked_encountered = True
                config.log.warning(
                    f"{provider_name} likely blocked request by safety policy "
                    f"(toxicity level: {level}). Retrying with lower level..."
                )
            else:
                config.log.exception(f"{provider_name} summary error at level {level}: %s", e)

    if not topics:
        if safety_blocked_encountered:
            # Return ironic message about safety filters only if we kept being blocked down to level 0
            ironic_messages = [
                f"<b>#Підсумки_дня — {escape(day_str)}</b>\n\n🤖 Ой, вибачте! Наш штучний розум вирішив, що ваші повідомлення занадто токсичні для його ніжної природи і відмовився їх аналізувати.\n\n😅 Спробуйте пізніше з командою <code>/summary_now 0</code> для більш дружелюбного стилю, або просто зачекайте — можливо, завтра він буде у кращому настрої!",
                f'<b>#Підсумки_дня — {escape(day_str)}</b>\n\n🛡️ Штучний інтелект активував режим "захист від токсичності" і відмовляється читати ваші повідомлення. Видимо, ви сьогодні були особливо "вибуховими"!\n\n🙃 Рекомендую спробувати <code>/summary_now 3</code> для більш м\'якого підходу.',
                f'<b>#Підсумки_дня — {escape(day_str)}</b>\n\n🚫 Штучний інтелект застрайкував: "Я не буду аналізувати цей рівень токсичності, знайдіть собі іншого бота!"\n\n😏 Спробуйте знизити градус до розумних меж командою <code>/summary_now 2</code>.',
            ]
            import random

            return random.choice(ironic_messages)
        return None

    header = f"<b>#Підсумки_дня — {escape(day_str)}</b>"
    items = []

    by_mid = {r["message_id"]: r for r in rows}
    by_uid = {}
    for r in rows:
        by_uid.setdefault(r["user_id"], r)

    for t in topics[:MAX_TOPICS_NUM]:
        title = clean_text(t.get("short_title") or "")
        summ = clean_text(t.get("summary") or "")
        mid = t.get("first_message_id")
        uid = t.get("initiator_user_id")

        if isinstance(mid, int) and mid in by_mid:
            msg_url = message_link(chat, mid)
            title_html = f'<a href="{msg_url}">{escape(title or "Тема")}</a>'
        else:
            title_html = escape(title or "Тема")

        urow = by_uid.get(uid) or {}
        initiator_html = user_link(
            user_id=urow.get("user_id", uid or 0),
            username=urow.get("username"),
            full_name=urow.get("full_name") or "Учасник",
        )

        line = f"• {title_html} — ініціатор {initiator_html}"
        if summ:
            line += f"\nКоротко: {escape(summ)}"
        items.append(line)

    return header + "\n\n" + "\n\n".join(items)
