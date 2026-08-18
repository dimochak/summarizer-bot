import asyncio
import re

from ddgs import DDGS
from pydantic import BaseModel, Field

import src.tools.config as config
from src.core.llm import get_structured_llm

# Regex: ботяндра, <query>, фас  (case-insensitive, flexible whitespace)
FAS_PATTERN = re.compile(
    r"ботяндр[аі][ік]?\s*[,!.:]?\s+(.+?)\s*[,!.:]?\s+фас\s*[!.]*$",
    re.IGNORECASE | re.DOTALL,
)


class SearchAnswer(BaseModel):
    response: str = Field(
        description="Саркастична відповідь українською з підсумком знайденого та посиланнями"
    )


async def search_web(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search DuckDuckGo and return a list of {title, href, body}.

    DDGS синхронний, тому виносимо його в потік: інакше пошук блокує event loop
    і бот перестає обробляти решту чату на весь час запиту.
    """

    def _search() -> list[dict[str, str]]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    return await asyncio.to_thread(_search)


async def summarize_results(
    query: str,
    results: list[dict[str, str]],
    chat_id: int,
) -> str:
    """Use AI to produce a sarcastic Ukrainian summary of search results."""

    if not results:
        return "Нічого не знайшов. Або інтернет зламався, або ти шукаєш щось, що не існує 🤷‍♂️"

    results_text = "\n\n".join(
        f"**{r.get('title', '')}**\n{r.get('body', '')}\n{r.get('href', '')}"
        for r in results
    )

    prompt = f"""Ти — дотепний український чат-бот.

Користувач попросив знайти інформацію: "{query}"

Ось результати пошуку:
{results_text}

Твоя задача:
1. Стисло підсумуй знайдене (3-5 речень), даючи реально корисну інформацію.
2. Додай трохи іронії та суржику у стилі Леся Подерев'янського.
3. В кінці додай 1-3 найрелевантніші посилання."""

    try:
        llm = get_structured_llm(SearchAnswer, chat_id=chat_id, purpose="summary")
        answer = await llm.ainvoke(prompt)
        return answer.response
    except Exception as e:
        config.log.exception("Error summarizing search results: %s", e)
        # Fallback: just list the results
        lines = [f"🔍 Результати для: <b>{query}</b>\n"]
        for r in results[:3]:
            lines.append(f"• <b>{r.get('title', '')}</b>\n  {r.get('body', '')}\n  {r.get('href', '')}")
        return "\n".join(lines)
