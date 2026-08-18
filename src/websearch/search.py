import re

import orjson as json
from ddgs import DDGS

import google.generativeai as genai
from openai import AsyncOpenAI

import src.tools.config as config

# Regex: ботяндра, <query>, фас  (case-insensitive, flexible whitespace)
FAS_PATTERN = re.compile(
    r"ботяндр[аі][ік]?\s*[,!.:]?\s+(.+?)\s*[,!.:]?\s+фас\s*[!.]*$",
    re.IGNORECASE | re.DOTALL,
)


def search_web(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search DuckDuckGo and return a list of {title, href, body}."""
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    return results


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
3. В кінці додай 1-3 найрелевантніші посилання.
4. Відповідай у JSON форматі: {{"response": "твоя відповідь тут"}}"""

    provider = _get_provider(chat_id)

    try:
        if provider == "openai":
            client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
            response = await client.chat.completions.create(
                model=config.OPENAI_MODEL_NAME,
                messages=[
                    {"role": "system", "content": "Ти дотепний український чат-бот. Відповідай у JSON."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            return data.get("response", "Щось пішло не так з відповіддю 🤖")

        elif provider == "gemini":
            model = genai.GenerativeModel(
                config.GEMINI_MODEL_NAME,
                generation_config={"response_mime_type": "application/json"},
            )
            response = model.generate_content(prompt)
            raw = response.text or ""
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return data.get("response", "Щось пішло не так 🤷‍♂️")
            return raw or "Не зміг обробити результати 😐"

    except Exception as e:
        config.log.exception("Error summarizing search results: %s", e)
        # Fallback: just list the results
        lines = [f"🔍 Результати для: <b>{query}</b>\n"]
        for r in results[:3]:
            lines.append(f"• <b>{r.get('title', '')}</b>\n  {r.get('body', '')}\n  {r.get('href', '')}")
        return "\n".join(lines)


def _get_provider(chat_id: int) -> str:
    if chat_id in config.OPENAI_CHAT_IDS:
        return "openai"
    elif chat_id in config.GEMINI_CHAT_IDS:
        return "gemini"
    elif config.GEMINI_API_KEY:
        return "gemini"
    elif config.OPENAI_API_KEY:
        return "openai"
    raise ValueError("No AI provider available")
