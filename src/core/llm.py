"""Єдина точка створення LLM-клієнтів.

До цього провайдер обирався в чотирьох місцях (panbot/engine/llm.py,
websearch/search.py, summarizer.py, petfinder) і викликався п'ятьма різними
способами — двома сирими SDK і LangChain. Зміна моделі означала правку
в п'яти файлах, а websearch мовчки залежав від того, що `genai.configure()`
встиг виконати імпортований раніше summarizer.

Тут усе зведено до двох функцій: `get_llm` і `get_structured_llm`.
"""
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

import src.tools.config as config

Provider = Literal["openai", "gemini"]
Purpose = Literal["chat", "decision", "summary", "traits"]

# Призначення, прив'язані до конкретного провайдера незалежно від налаштувань чату.
# Traits історично працювали лише через OpenAI; перемикання на Gemini —
# окреме продуктове рішення, а не частина уніфікації.
_FORCED_PROVIDER: dict[str, Provider] = {
    "traits": "openai",
}


def resolve_provider(chat_id: int | None = None, purpose: Purpose = "chat") -> Provider:
    """Який провайдер обслуговує цей чат і це призначення."""
    forced = _FORCED_PROVIDER.get(purpose)
    if forced:
        return forced

    if chat_id is not None:
        if chat_id in config.OPENAI_CHAT_IDS:
            return "openai"
        if chat_id in config.GEMINI_CHAT_IDS:
            return "gemini"

    # Чат не закріплений за провайдером — беремо той, для якого є ключ.
    return "gemini" if config.GEMINI_API_KEY else "openai"


def resolve_model_name(provider: Provider, purpose: Purpose) -> str:
    if provider == "openai":
        if purpose == "decision":
            return config.REPLY_DECISION_OPENAI_MODEL_NAME or config.OPENAI_MODEL_NAME
        if purpose == "traits":
            return config.TRAITS_MODEL_NAME
        return config.OPENAI_MODEL_NAME

    if purpose == "decision":
        return config.REPLY_DECISION_GEMINI_MODEL_NAME or config.GEMINI_MODEL_NAME
    return config.GEMINI_MODEL_NAME


def get_llm(
    chat_id: int | None = None,
    purpose: Purpose = "chat",
    provider: Provider | None = None,
) -> BaseChatModel:
    """Клієнт LLM для заданого чату й призначення.

    `provider` перекриває автовизначення — потрібно там, де виклик не прив'язаний
    до чату взагалі.
    """
    provider = provider or resolve_provider(chat_id, purpose)
    model_name = resolve_model_name(provider, purpose)

    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise RuntimeError(f"OPENAI_API_KEY не заданий, а purpose={purpose} потребує OpenAI")
        return ChatOpenAI(model=model_name, api_key=config.OPENAI_API_KEY)

    if not config.GEMINI_API_KEY:
        raise RuntimeError(f"GEMINI_API_KEY не заданий, а purpose={purpose} потребує Gemini")
    return ChatGoogleGenerativeAI(model=model_name, api_key=config.GEMINI_API_KEY)


def get_structured_llm(
    schema,
    chat_id: int | None = None,
    purpose: Purpose = "chat",
    provider: Provider | None = None,
):
    """LLM, що повертає готовий об'єкт за Pydantic-схемою.

    Замінює ручний `response_format={"type": "json_object"}` + `json.loads`
    і регулярку `re.search(r"\\{.*\\}")`, якою з Gemini виколупували JSON.
    """
    return get_llm(chat_id=chat_id, purpose=purpose, provider=provider).with_structured_output(schema)
