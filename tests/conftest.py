"""Спільна підготовка тестового оточення.

Головне тут — тести не повинні залежати ні від `.env`, ні від живої Postgres.
Обидві залежності раніше робили набір тестів незапускабельним у CI.
"""
import os

import pytest

# Має бути виставлено ДО імпорту src.tools.config, який читає os.environ на рівні модуля.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("LOG_FILENAME", "/dev/null")


@pytest.fixture(autouse=True)
def no_real_db(request, monkeypatch):
    """Будь-яка спроба відкрити справжнє з'єднання — це помилка тесту.

    Без цього незамокані виклики БД падали з ConnectionRefused, і причину
    доводилось шукати в трейсбеку psycopg замість імені незамоканої функції.
    """
    if "integration" in request.keywords:
        yield None  # інтеграційні тести ходять у справжню БД свідомо
        return

    import src.tools.db as db_mod

    def _forbidden(*args, **kwargs):
        raise RuntimeError(
            "Тест спробував відкрити справжнє з'єднання з БД. "
            "Замокай потрібну функцію з src.tools.db."
        )

    monkeypatch.setattr(db_mod, "db", _forbidden)
    yield _forbidden


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """Жоден тест не повинен ходити в справжній LLM.

    Раніше test_father_sets_custom_role мовчки робив реальний HTTP-запит, бо його
    повідомлення не збігалося з тригером у коді і виконання провалювалось у генерацію.
    """
    from src.panbot.engine.llm import LLMEngine

    def _forbidden(*args, **kwargs):
        raise RuntimeError(
            "Тест спробував викликати справжній LLM. "
            "Замокай PanBotEngine.generate_response або відповідний метод LLMEngine."
        )

    monkeypatch.setattr(LLMEngine, "get_llm", _forbidden)
    monkeypatch.setattr(LLMEngine, "get_structured_llm", _forbidden)
    monkeypatch.setattr(LLMEngine, "get_reply_decision_llm", _forbidden)
