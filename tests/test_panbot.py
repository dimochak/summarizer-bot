from datetime import datetime, timezone
import pytest
import src.tools.config as config
from src.tools.handlers import get_panbot_response, should_reply, should_reply_with_agent
from src.panbot.exceptions import SarcasmLimitExceeded

class FakeMessage:
    def __init__(self, text, user_id, message_id, reply_to_message_id=None, date=None, chat_id=1):
        self.text = text
        self.caption = None
        self.from_user = type("User", (), {"id": user_id, "full_name": "Test User"})
        self.message_id = message_id
        self.reply_to_message_id = reply_to_message_id
        self.date = date or datetime.now(timezone.utc)
        self.chat = type("Chat", (), {"id": chat_id})
        self.reply_to_message = None
        if reply_to_message_id:
            self.reply_to_message = type("Reply", (), {"message_id": reply_to_message_id, "text": "some text", "from_user": None, "date": self.date})

DAILY_LIMIT = 10 # За замовчуванням в конфигу зазвичай 10

def test_no_trigger_no_reply():
    msg = FakeMessage("random text", user_id=123, message_id=1)
    assert should_reply(msg) is False

def test_trigger_and_reply():
    msg = FakeMessage("Пан бот, привіт", user_id=123, message_id=1)
    assert should_reply(msg) is True


def test_should_reply_with_agent_skips_when_should_reply_false(monkeypatch):
    async def mock_decide(*args, **kwargs):
        raise AssertionError("Agent should not be called when should_reply is False")

    from src.tools import handlers as handlers_mod
    monkeypatch.setattr(handlers_mod.panbot_engine, "should_reply_by_agent", mock_decide)

    msg = FakeMessage("random text", user_id=123, message_id=1)

    import asyncio
    result = asyncio.run(should_reply_with_agent(msg))
    assert result is None


def test_should_reply_with_agent_uses_agent_decision(monkeypatch):
    async def mock_decide(*args, **kwargs):
        return False

    from src.tools import handlers as handlers_mod
    monkeypatch.setattr(handlers_mod.panbot_engine, "should_reply_by_agent", mock_decide)

    msg = FakeMessage("Пан бот, привіт", user_id=123, message_id=1)

    import asyncio
    result = asyncio.run(should_reply_with_agent(msg))
    assert result is False

def test_daily_limit_enforced(monkeypatch):
    import src.tools.handlers as handlers_mod
    import src.panbot.helpers as helpers_mod
    
    # Mock generation to avoid actual API calls
    async def mock_gen_resp(self, message, quoted_block, traits_block, user_name,
                            user_message, custom_role=None, is_creator=False):
        return "mocked response"
    
    # We need to mock PanBotEngine.generate_response
    from src.panbot.engine.core import PanBotEngine
    monkeypatch.setattr(PanBotEngine, "generate_response", mock_gen_resp)
    
    # Mock DB functions to avoid DB connection
    mock_usage = 0
    def mock_get_usage(u, c, d):
        return mock_usage
    def mock_increment_usage(u, c, d):
        nonlocal mock_usage
        mock_usage += 1
        return mock_usage
    
    def mock_get_traits(user_id):
        return {}

    monkeypatch.setattr(handlers_mod, "get_panbot_usage", mock_get_usage)
    monkeypatch.setattr(handlers_mod, "increment_panbot_usage", mock_increment_usage)
    monkeypatch.setattr(handlers_mod, "get_custom_role", lambda c: None)
    monkeypatch.setattr(helpers_mod, "get_user_traits", mock_get_traits)
    
    # Mock config limit
    monkeypatch.setattr(config, "MESSAGES_PER_USER", 2)
    
    reply = FakeMessage("Пан бот, круто?", user_id=555, message_id=10, reply_to_message_id=1)
    
    import asyncio
    async def run():
        # First 2 should pass
        await get_panbot_response(reply)
        await get_panbot_response(reply)
        # Third should fail
        with pytest.raises(SarcasmLimitExceeded):
            await get_panbot_response(reply)
            
    asyncio.run(run())

def test_context_includes_bot_messages(monkeypatch):
    from src.panbot.history.manager import ChatContextHistory
    from langchain_core.messages import AIMessage, HumanMessage

    # Mock DB to return messages including one from bot
    def mock_fetch(self, start, end):
        return [
            {"text": "Привіт", "full_name": "User", "username": "user", "ts_utc": 100, "user_id": 123, "message_id": 1, "reply_to_message_id": None},
            {"text": "Я бот", "full_name": "PanBot", "username": None, "ts_utc": 110, "user_id": config.BOT_USER_ID, "message_id": 2, "reply_to_message_id": 1},
            {"text": "Що ти сказав?", "full_name": "User", "username": "user", "ts_utc": 120, "user_id": 123, "message_id": 3, "reply_to_message_id": 2},
        ]
    
    monkeypatch.setattr(ChatContextHistory, "_fetch_messages", mock_fetch)
    
    history = ChatContextHistory(chat_id=1, current_message_id=4)
    messages = history.messages
    
    # Rows are returned in DESC order from _fetch_messages and then reversed in .messages
    # So the order in mock_fetch should be DESC if we want them to be reversed to chronological
    
    # Wait, let's re-read ChatContextHistory.messages:
    # for row in rows[::-1]:  # Хронологічно
    
    # If _fetch_messages returns [3, 2, 1] (DESC), then rows[::-1] is [1, 2, 3] (ASC).
    
    def mock_fetch_desc(self, start, end):
        return [
            {"text": "Що ти сказав?", "full_name": "User", "username": "user", "ts_utc": 120, "user_id": 123, "message_id": 3, "reply_to_message_id": 2},
            {"text": "Я бот", "full_name": "PanBot", "username": None, "ts_utc": 110, "user_id": config.BOT_USER_ID, "message_id": 2, "reply_to_message_id": 1},
            {"text": "Привіт", "full_name": "User", "username": "user", "ts_utc": 100, "user_id": 123, "message_id": 1, "reply_to_message_id": None},
        ]
    monkeypatch.setattr(ChatContextHistory, "_fetch_messages", mock_fetch_desc)
    
    history = ChatContextHistory(chat_id=1, current_message_id=4)
    messages = history.messages
    
    assert len(messages) == 3
    assert isinstance(messages[0], HumanMessage)
    assert isinstance(messages[1], AIMessage)
    assert messages[1].content == "Я бот"
    assert isinstance(messages[2], HumanMessage)

def test_context_includes_replied_message_even_if_old(monkeypatch):
    """Явний реплай має підтягувати повідомлення незалежно від його віку.

    ts_utc=10 — це 1970 рік, тобто далеко поза 48-годинним вікном, яке
    обмежує загальний фон чату.
    """
    from src.panbot.history.manager import ChatContextHistory

    def mock_fetch(self, start, end):
        return []

    def mock_chain(self, message_id, max_depth=25):
        if message_id == 1:
            return [{"text": "Старе повідомлення", "full_name": "Old User", "username": "old", "ts_utc": 10, "user_id": 999, "message_id": 1, "reply_to_message_id": None}]
        return []

    monkeypatch.setattr(ChatContextHistory, "_fetch_messages", mock_fetch)
    monkeypatch.setattr(ChatContextHistory, "_fetch_reply_chain", mock_chain)

    # User replies to message ID 1
    history = ChatContextHistory(chat_id=1, current_message_id=4, reply_to_id=1)
    messages = history.messages

    assert len(messages) == 1
    assert messages[0].content == "Old User: Старе повідомлення"


def test_general_context_not_fetched_when_thread_exists(monkeypatch):
    """Загальний фон не тягнемо, коли є тред — раніше запит на 2000 рядків
    виконувався завжди, а результат викидався."""
    from src.panbot.history.manager import ChatContextHistory

    calls = []

    def mock_fetch(self, start, end):
        calls.append((start, end))
        return []

    def mock_chain(self, message_id, max_depth=25):
        return [{"text": "У треді", "full_name": "User", "username": "u", "ts_utc": 100, "user_id": 5, "message_id": 1, "reply_to_message_id": None}]

    monkeypatch.setattr(ChatContextHistory, "_fetch_messages", mock_fetch)
    monkeypatch.setattr(ChatContextHistory, "_fetch_reply_chain", mock_chain)

    history = ChatContextHistory(chat_id=1, current_message_id=4, reply_to_id=1)
    assert len(history.messages) == 1
    assert calls == []

def test_should_reply_on_comment_request(monkeypatch):
    # Reply to some user (not bot) but asking to comment
    msg = FakeMessage("Прокоментуй це", user_id=123, message_id=2, reply_to_message_id=1)

    # helpers імпортує is_bot_message у свій простір імен, тож патчити треба саме там —
    # попередня версія тесту патчила src.tools.db і не мала жодного ефекту.
    import src.panbot.helpers as helpers_mod
    monkeypatch.setattr(helpers_mod, "is_bot_message", lambda cid, mid: False)

    assert should_reply(msg) is True

def test_father_respect_injection(monkeypatch):
    import src.tools.handlers as handlers_mod
    
    # Пошана до творця тепер передається прапорцем is_creator і застосовується
    # в шаблоні system.j2, а не вшивається в traits_block.
    captured = []
    async def mock_gen_resp(self, message, quoted_block, traits_block, user_name,
                            user_message, custom_role=None, is_creator=False):
        captured.append(is_creator)
        return "mocked response"
    
    from src.panbot.engine.core import PanBotEngine
    monkeypatch.setattr(PanBotEngine, "generate_response", mock_gen_resp)
    
    # Mock DB functions
    monkeypatch.setattr(handlers_mod, "get_panbot_usage", lambda u, c, d: 0)
    monkeypatch.setattr(handlers_mod, "increment_panbot_usage", lambda u, c, d: 1)
    monkeypatch.setattr(handlers_mod, "get_custom_role", lambda c: None)
    
    import src.panbot.helpers as helpers_mod
    monkeypatch.setattr(helpers_mod, "get_user_traits", lambda u: {})

    import asyncio
    
    # Test for "father" ID
    father_msg = FakeMessage("Привіт, сину", user_id=229953580, message_id=100)
    asyncio.run(handlers_mod.get_panbot_response(father_msg))
    
    assert captured[0] is True

    # Test for normal user
    normal_msg = FakeMessage("Привіт, бот", user_id=123, message_id=101)
    asyncio.run(handlers_mod.get_panbot_response(normal_msg))
    
    assert captured[1] is False

def test_father_sets_custom_role(monkeypatch):
    import src.tools.handlers as handlers_mod
    import src.tools.db as db_mod

    # Mock DB functions
    roles = {}
    def mock_set_role(chat_id, role):
        roles[chat_id] = role
    def mock_get_role(chat_id):
        return roles.get(chat_id)

    monkeypatch.setattr(handlers_mod, "set_custom_role", mock_set_role)
    monkeypatch.setattr(handlers_mod, "get_custom_role", mock_get_role)
    monkeypatch.setattr(handlers_mod, "get_panbot_usage", lambda u, c, d: 0)
    monkeypatch.setattr(handlers_mod, "increment_panbot_usage", lambda u, c, d: 1)

    import src.panbot.helpers as helpers_mod
    monkeypatch.setattr(helpers_mod, "get_user_traits", lambda u: {})

    import asyncio

    # Father sets role
    father_msg = FakeMessage("ботяндра, твоя нова роль Ти тепер котик", user_id=229953580, message_id=100)
    response = asyncio.run(handlers_mod.get_panbot_response(father_msg))

    assert "Тепер моя роль: Ти тепер котик" in response
    assert roles[1] == "Ти тепер котик"

    # Normal user tries to set role (should be ignored and passed to LLM)
    captured_custom_roles = []
    async def mock_gen_resp(self, message, quoted_block, traits_block, user_name,
                            user_message, custom_role=None, is_creator=False):
        captured_custom_roles.append(custom_role)
        return "mocked response"
    
    from src.panbot.engine.core import PanBotEngine
    monkeypatch.setattr(PanBotEngine, "generate_response", mock_gen_resp)
    monkeypatch.setattr(handlers_mod, "get_traits_block", lambda u: "")
    monkeypatch.setattr(handlers_mod, "get_quoted_block", lambda m: "")

    normal_msg = FakeMessage("ботяндра, твоя нова роль Ти тепер пес", user_id=123, message_id=101)
    asyncio.run(handlers_mod.get_panbot_response(normal_msg))

    # Role should NOT have changed in DB for normal user's message
    # And it should have passed the EXISTING role to LLM
    assert roles[1] == "Ти тепер котик"
    assert captured_custom_roles[0] == "Ти тепер котик"
