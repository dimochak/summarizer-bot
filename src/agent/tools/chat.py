"""Інструменти, що працюють з історією самого чату."""
from datetime import datetime, timezone, timedelta
from typing import Literal

from langchain_core.tools import BaseTool, tool

import src.tools.config as config
from src.panbot.engine.summary import SummaryEngine
from src.tools.db import (
    db_call,
    find_chat_participants,
    get_user_recent_messages,
    get_user_traits,
    search_messages,
)

_summary_engine = SummaryEngine()

MAX_SEARCH_RESULTS = 20


def build_chat_tools(chat_id: int) -> list[BaseTool]:
    """Інструменти, замкнені на конкретний чат.

    chat_id не є аргументом інструмента свідомо: інакше модель могла б
    підставити чужий і дістати історію сусіднього чату.
    """

    @tool
    async def summarize_chat(
        period: Literal["messages", "hours", "minutes"],
        amount: int,
    ) -> str:
        """Переказує, ПРО ЩО ГОВОРИЛИ в цьому чаті за останній відрізок часу.

        Використовуй ЛИШЕ коли просять переказати перебіг розмови:
        «підсумуй останні 100 повідомлень», «що було за 5 годин», «що я
        пропустив».

        НЕ використовуй для питань про зовнішній світ (ціни, новини, погода)
        і НЕ використовуй, щоб «підсумувати» щось інше — цей інструмент уміє
        тільки переказ повідомлень цього чату.

        Args:
            period: у чому вимірюється відрізок — кількість повідомлень, годин чи хвилин.
            amount: скільки саме. Обмежується розумною стелею автоматично.
        """
        limits = {"messages": 2000, "hours": 48, "minutes": 2880}
        amount = max(1, min(int(amount), limits[period]))
        return await _summary_engine.get_summary(
            chat_id=chat_id, request_type=period, value=amount
        )

    @tool
    async def search_chat_history(query: str, days_back: int = 30) -> str:
        """Шукає в історії ЦЬОГО чату повідомлення за ключовими словами.

        Використовуй, коли питають про сказане в чаті раніше:
        «коли ми говорили про переїзд», «що Петро казав про ту машину».
        Повертає знайдені повідомлення з автором і датою.

        Це пошук ПО ЧАТУ, а не по інтернету: для фактів про зовнішній світ
        він не годиться.

        Args:
            query: ключові слова для пошуку.
            days_back: як глибоко шукати. Історія зберігається обмежений час.
        """
        since = int(
            (datetime.now(timezone.utc) - timedelta(days=max(1, days_back))).timestamp()
        )
        rows = await db_call(search_messages, chat_id, query, since, MAX_SEARCH_RESULTS)
        if not rows:
            return f"За запитом «{query}» нічого не знайдено в історії чату."

        lines = []
        for r in rows:
            when = datetime.fromtimestamp(r["ts_utc"], tz=timezone.utc).astimezone(
                config.KYIV
            )
            who = r["full_name"] or r["username"] or "Учасник"
            lines.append(f"[{when:%d.%m %H:%M}] {who}: {r['text']}")

        return f"Знайдено {len(rows)} повідомлень за запитом «{query}»:\n" + "\n".join(lines)

    @tool
    async def describe_participant(name: str) -> str:
        """Дає профіль учасника ЦЬОГО чату: тон, теми, манера спілкування.

        Використовуй, коли просять схарактеризувати когось із чату, розповісти
        про учасника, порівняти людей, або питають «що це за людина».

        Імена в чаті — це майже завжди свої учасники, а не публічні особи,
        тож про них НЕ треба шукати в інтернеті.

        Args:
            name: імʼя або юзернейм так, як його назвали в повідомленні.
        """
        people = await db_call(find_chat_participants, chat_id, name)
        if not people:
            return f"Учасника на імʼя «{name}» у цьому чаті не знайдено."

        blocks = []
        for person in people:
            who = person["full_name"] or person["username"] or f"id{person['user_id']}"
            traits = await db_call(get_user_traits, person["user_id"])

            if traits:
                tone = traits.get("tone") or {}
                style = traits.get("style") or {}
                topics = ", ".join(
                    t.get("name", "") for t in (traits.get("topics") or [])[:6]
                )
                blocks.append(
                    f"{who} (повідомлень у чаті: {person['messages_count']}):\n"
                    f"  Опис: {traits.get('summary', '—')}\n"
                    f"  Теми: {topics or '—'}\n"
                    f"  Тон: доброзичливість {tone.get('friendliness', '?')}, "
                    f"сарказм {tone.get('sarcasm', '?')}, "
                    f"токсичність {tone.get('toxicity', '?')}\n"
                    f"  Манера: багатослівність {style.get('verbosity', '?')}, "
                    f"емодзі {style.get('emoji_usage', '?')}"
                )
                continue

            # Профіль ще не побудований — джоб оновлення міг до нього не дійти.
            # Краще віддати сирі репліки, ніж нічого.
            recent = await db_call(get_user_recent_messages, chat_id, person["user_id"])
            quotes = "\n".join(f"  — {r['text']}" for r in recent[:15] if r["text"])
            blocks.append(
                f"{who} (повідомлень у чаті: {person['messages_count']}): "
                f"готового профілю ще немає, ось останні репліки:\n{quotes or '  —'}"
            )

        return "\n\n".join(blocks)

    return [summarize_chat, search_chat_history, describe_participant]
