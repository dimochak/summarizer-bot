"""Тести єдиної фабрики LLM.

Перевіряємо саме те, що раніше було розмазане по п'яти модулях: вибір
провайдера за chat_id і вибір моделі за призначенням.
"""
import pytest

import src.tools.config as config
from src.core import llm as llm_mod


@pytest.fixture(autouse=True)
def chat_routing(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_CHAT_IDS", {111})
    monkeypatch.setattr(config, "GEMINI_CHAT_IDS", {222})
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-openai")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-gemini")
    monkeypatch.setattr(config, "OPENAI_MODEL_NAME", "gpt-test")
    monkeypatch.setattr(config, "GEMINI_MODEL_NAME", "gemini-test")
    monkeypatch.setattr(config, "REPLY_DECISION_OPENAI_MODEL_NAME", "")
    monkeypatch.setattr(config, "REPLY_DECISION_GEMINI_MODEL_NAME", "")
    monkeypatch.setattr(config, "VISION_MODEL_NAME", "vision-test")
    monkeypatch.setattr(config, "TRAITS_MODEL_NAME", "traits-test")


def test_chat_id_selects_openai():
    assert llm_mod.resolve_provider(111) == "openai"


def test_chat_id_selects_gemini():
    assert llm_mod.resolve_provider(222) == "gemini"


def test_unknown_chat_falls_back_to_available_key():
    assert llm_mod.resolve_provider(999) == "gemini"


def test_unknown_chat_falls_back_to_openai_without_gemini_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert llm_mod.resolve_provider(999) == "openai"


@pytest.mark.parametrize("chat_id", [111, 222, 999, None])
def test_vision_is_always_openai(chat_id):
    """Vision історично лише на OpenAI — налаштування чату його не перемикає."""
    assert llm_mod.resolve_provider(chat_id, "vision") == "openai"


@pytest.mark.parametrize("chat_id", [111, 222, 999, None])
def test_traits_is_always_openai(chat_id):
    assert llm_mod.resolve_provider(chat_id, "traits") == "openai"


def test_decision_falls_back_to_main_model_when_unset():
    assert llm_mod.resolve_model_name("openai", "decision") == "gpt-test"
    assert llm_mod.resolve_model_name("gemini", "decision") == "gemini-test"


def test_decision_uses_dedicated_model_when_set(monkeypatch):
    monkeypatch.setattr(config, "REPLY_DECISION_OPENAI_MODEL_NAME", "gpt-mini-test")
    assert llm_mod.resolve_model_name("openai", "decision") == "gpt-mini-test"


def test_purpose_specific_models():
    assert llm_mod.resolve_model_name("openai", "vision") == "vision-test"
    assert llm_mod.resolve_model_name("openai", "traits") == "traits-test"
    assert llm_mod.resolve_model_name("openai", "summary") == "gpt-test"
    assert llm_mod.resolve_model_name("gemini", "summary") == "gemini-test"


def test_missing_key_raises_with_clear_message(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm_mod.get_llm(purpose="vision")


def test_get_llm_builds_expected_client():
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI

    assert isinstance(llm_mod.get_llm(111), ChatOpenAI)
    assert isinstance(llm_mod.get_llm(222), ChatGoogleGenerativeAI)


def test_explicit_provider_overrides_chat_routing():
    from langchain_openai import ChatOpenAI

    # Чат 222 налаштований на Gemini, але виклик не прив'язаний до чату.
    assert isinstance(llm_mod.get_llm(222, provider="openai"), ChatOpenAI)
