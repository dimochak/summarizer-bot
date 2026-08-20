"""Тести агентного ядра — етапу збору фактів.

Перевіряємо контракт, а не саму модель: інструменти замкнені на свій чат,
відсутність фактів дає порожній рядок, збій агента не валить відповідь.
"""
import pytest

import src.tools.config as config
from src.agent import graph as graph_mod
from src.agent.tools import build_tools


def test_tools_are_bound_to_their_chat():
    """chat_id не має бути аргументом, який модель може підставити сама."""
    tools = build_tools(chat_id=42)
    names = {t.name for t in tools}
    assert names == {"summarize_chat", "search_chat_history", "get_weather", "web_fetch"}

    for t in tools:
        assert "chat_id" not in t.args, (
            f"{t.name} приймає chat_id від моделі — можна дістати чужий чат"
        )


def test_every_tool_has_a_description():
    """Без опису модель не знає, коли інструмент доречний."""
    for t in build_tools(chat_id=1):
        assert t.description and len(t.description) > 30, t.name


class FakeAgent:
    def __init__(self, final_text, tool_calls=None):
        self.final_text = final_text
        self.tool_calls = tool_calls or []
        self.seen_config = None

    async def ainvoke(self, state, config=None):
        self.seen_config = config
        message = type("Msg", (), {"content": self.final_text, "tool_calls": []})
        return {"messages": [*state["messages"], message]}


@pytest.fixture
def fake_agent(monkeypatch):
    def install(final_text):
        agent = FakeAgent(final_text)
        monkeypatch.setattr(graph_mod, "create_agent", lambda **kw: agent)
        monkeypatch.setattr(graph_mod, "get_llm", lambda **kw: object())
        return agent

    return install


async def test_no_tools_needed_returns_empty(fake_agent):
    """NONE від агента означає «інструменти не потрібні» — далі йде звичайна відповідь."""
    fake_agent("NONE")
    assert await graph_mod.gather_facts(1, "просто побалакаємо") == ""


async def test_empty_answer_returns_empty(fake_agent):
    fake_agent("   ")
    assert await graph_mod.gather_facts(1, "привіт") == ""


async def test_facts_are_returned(fake_agent):
    fake_agent("Київ: +18°C, дощ")
    assert await graph_mod.gather_facts(1, "яка погода в Києві?") == "Київ: +18°C, дощ"


async def test_step_limit_is_passed(fake_agent):
    agent = fake_agent("NONE")
    await graph_mod.gather_facts(1, "щось")
    assert agent.seen_config["recursion_limit"] == config.AGENT_MAX_STEPS * 2


async def test_agent_failure_does_not_break_the_reply(monkeypatch):
    """Збій агента має лишати бота здатним відповісти без фактів."""
    import src.tools.handlers as handlers_mod
    from src.panbot.engine.core import PanBotEngine

    captured = []

    async def failing_agent(chat_id, user_message):
        raise RuntimeError("агент впав")

    async def mock_gen_resp(self, **kwargs):
        captured.append(kwargs["facts_block"])
        return "відповідь"

    monkeypatch.setattr(handlers_mod, "gather_facts", failing_agent)
    monkeypatch.setattr(PanBotEngine, "generate_response", mock_gen_resp)
    monkeypatch.setattr(config, "AGENT_CHAT_IDS", {1})
    monkeypatch.setattr(handlers_mod, "get_panbot_usage", lambda u, c, d: 0)
    monkeypatch.setattr(handlers_mod, "increment_panbot_usage", lambda u, c, d: 1)
    monkeypatch.setattr(handlers_mod, "get_custom_role", lambda c: None)

    import src.panbot.helpers as helpers_mod
    monkeypatch.setattr(helpers_mod, "get_user_traits", lambda u: {})

    from tests.test_panbot import FakeMessage

    msg = FakeMessage("яка погода?", user_id=5, message_id=1)
    assert await handlers_mod.get_panbot_response(msg) == "відповідь"
    assert captured == [""], "без фактів має піти порожній блок, а не падіння"


async def test_agent_is_skipped_for_chats_without_the_flag(monkeypatch):
    """Прапорець дозволяє викотити агента на один чат і порівняти з рештою."""
    import src.tools.handlers as handlers_mod
    from src.panbot.engine.core import PanBotEngine

    async def fail(*args, **kwargs):
        raise AssertionError("агент не мав викликатись у чаті без прапорця")

    async def mock_gen_resp(self, **kwargs):
        return "відповідь"

    monkeypatch.setattr(handlers_mod, "gather_facts", fail)
    monkeypatch.setattr(PanBotEngine, "generate_response", mock_gen_resp)
    monkeypatch.setattr(config, "AGENT_CHAT_IDS", set())
    monkeypatch.setattr(handlers_mod, "get_panbot_usage", lambda u, c, d: 0)
    monkeypatch.setattr(handlers_mod, "increment_panbot_usage", lambda u, c, d: 1)
    monkeypatch.setattr(handlers_mod, "get_custom_role", lambda c: None)

    import src.panbot.helpers as helpers_mod
    monkeypatch.setattr(helpers_mod, "get_user_traits", lambda u: {})

    from tests.test_panbot import FakeMessage

    msg = FakeMessage("яка погода?", user_id=5, message_id=1)
    assert await handlers_mod.get_panbot_response(msg) == "відповідь"


def test_native_search_is_counted_in_used_tools():
    """Вбудований пошук приходить блоком у content, а не в tool_calls."""
    message = type("M", (), {
        "tool_calls": [],
        "content": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "text", "text": "результат"},
        ],
    })
    assert graph_mod._used_tools([message]) == ["web_search_call"]


def test_local_and_native_tools_are_both_counted():
    local = type("M", (), {"tool_calls": [{"name": "get_weather"}], "content": ""})
    native = type("M", (), {"tool_calls": [], "content": [{"type": "web_search_call"}]})
    assert graph_mod._used_tools([local, native]) == ["get_weather", "web_search_call"]


def test_text_property_is_preferred_over_method():
    """LangChain перевів .text із методу на property — підтримуємо обидва."""
    modern = type("M", (), {"text": "нове API", "content": "ігнорується"})()
    assert graph_mod._extract_text(modern) == "нове API"


def test_text_extracted_from_content_blocks():
    blocks = type("M", (), {
        "content": [
            {"type": "web_search_call"},
            {"type": "text", "text": "перший"},
            {"type": "text", "text": "другий"},
        ],
    })()
    # без .text взагалі — падати не має
    assert "перший" in graph_mod._extract_text(blocks)
    assert "другий" in graph_mod._extract_text(blocks)
