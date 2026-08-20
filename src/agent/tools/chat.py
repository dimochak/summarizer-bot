"""Інструменти, що працюють з історією самого чату."""
from datetime import datetime, timezone, timedelta
from typing import Literal

from langchain_core.tools import BaseTool, tool

import src.tools.config as config
from src.panbot.engine.summary import SummaryEngine
from src.tools.db import db_call, search_messages

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
        """Підсумовує недавні повідомлення цього чату.

        Використовуй, коли просять підсумок або переказ того, що відбувалось:
        «підсумуй останні 100 повідомлень», «що було за 5 годин».

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
        """Шукає в історії цього чату повідомлення за ключовими словами.

        Використовуй, коли питають про те, що обговорювали раніше:
        «коли ми говорили про переїзд», «що Петро казав про ту машину».
        Повертає знайдені повідомлення з автором і датою.

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

    return [summarize_chat, search_chat_history]
