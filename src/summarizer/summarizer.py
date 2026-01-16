import re
import orjson as json
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import closing
from html import escape

import google.generativeai as genai
import tiktoken
from openai import AsyncOpenAI

from telegram import Chat
from telegram.ext import ContextTypes

import src.tools.config as config
from src.tools.db import db
from src.tools.utils import utc_ts, clean_text, message_link, user_link

genai.configure(api_key=config.GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    config.GEMINI_MODEL_NAME,
    generation_config={"response_mime_type": "application/json"},
)
openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

MAX_TOPICS_NUM = 7


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


async def get_openai_summary(prompt: str) -> dict:
    """Get summary from OpenAI"""
    config.log.info(f"OpenAI prompt: {prompt}")
    try:
        response = await openai_client.chat.completions.create(
            model=config.OPENAI_MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": "Ти — надзвичайно саркастичний та їдкий помічник, що групує повідомлення чату у теми за календарний день. Завжди відповідай у форматі JSON",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        config.log.exception("OpenAI API error: %s", e)
        raise


async def get_gemini_summary(prompt: str) -> dict:
    """Get summary from Gemini"""
    try:
        config.log.info(f"Gemini prompt: {prompt}")
        resp = gemini_model.generate_content(prompt)
        raw = resp.text or ""
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        return data
    except Exception as e:
        config.log.exception("Gemini API error: %s", e)
        raise


def should_use_openai(chat_id: int) -> bool:
    """Determine if we should use OpenAI for this chat"""
    return chat_id in config.OPENAI_CHAT_IDS


def should_use_gemini(chat_id: int) -> bool:
    """Determine if we should use Gemini for this chat"""
    return chat_id in config.GEMINI_CHAT_IDS


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
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT * FROM messages WHERE chat_id=%s AND ts_utc>=%s AND ts_utc<%s ORDER BY ts_utc ASC",
            (chat.id, utc_ts(start_utc), utc_ts(end_utc)),
        )
        rows = [dict(r) for r in cur.fetchall()]

    rows = [r for r in rows if clean_text(r["text"])]
    if not rows:
        return None

    snippet = build_messages_snippet(rows)
    day_str = (start_local.date()).strftime("%d.%m.%Y")

    # Determine which AI provider to use
    use_openai = should_use_openai(chat.id)
    use_gemini = should_use_gemini(chat.id)

    if use_openai:
        provider_name = "OpenAI"
    elif use_gemini:
        provider_name = "Gemini"
    else:
        config.log.error(
            f"Chat {chat.id} is in ALLOWED_CHAT_IDS but not in any provider-specific list"
        )
        return None

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
            if use_openai:
                data = await get_openai_summary(prompt)
            else:
                data = await get_gemini_summary(prompt)

            topics = data.get("topics", [])
            if topics:
                toxicity_level = level  # record the actual level that worked
                break
            # If no topics returned, try a lower toxicity just in case model was overly strict
            config.log.warning(
                f"{provider_name} returned no topics at toxicity level {level}, trying lower level..."
            )
        except ValueError as e:
            # Heuristic: detect safety filter blocking or similar conditions and retry with lower level
            if (
                "response to contain a valid `Part`" in str(e)
                or "finish_reason" in str(e)
                or "content_filter" in str(e)
            ):
                safety_blocked_encountered = True
                config.log.warning(
                    f"{provider_name} blocked request due to safety policy (toxicity level: {level}). Retrying with lower level..."
                )
                continue
            else:
                config.log.exception(f"{provider_name} summary error: %s", e)
                return None
        except Exception as e:
            config.log.exception(f"{provider_name} summary error: %s", e)
            return None

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
