from datetime import datetime, timezone
import pytest
import src.tools.config as config
from src.tools.handlers import get_panbot_response, should_reply
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

def test_daily_limit_enforced(monkeypatch):
    import src.tools.handlers as handlers_mod
    import src.panbot.helpers as helpers_mod
    
    # Mock generation to avoid actual API calls
    async def mock_gen_resp(self, message, quoted_block, traits_block, user_name, user_message):
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
    from src.panbot.history.manager import ChatContextHistory
    from src.tools.db import get_message_by_id
    from langchain_core.messages import HumanMessage

    # Mock DB to return empty for recent messages
    def mock_fetch(self, start, end):
        return []
    
    # Mock DB to return the old message
    def mock_get_msg(chat_id, message_id):
        if message_id == 1:
            return {"text": "Старе повідомлення", "full_name": "Old User", "username": "old", "ts_utc": 10, "user_id": 999, "message_id": 1, "reply_to_message_id": None}
        return None

    monkeypatch.setattr(ChatContextHistory, "_fetch_messages", mock_fetch)
    import src.panbot.history.manager as history_mod
    monkeypatch.setattr(history_mod, "get_message_by_id", mock_get_msg)
    
    # User replies to message ID 1
    history = ChatContextHistory(chat_id=1, current_message_id=4, reply_to_id=1)
    messages = history.messages
    
    assert len(messages) == 1
    assert messages[0].content == "Old User: Старе повідомлення"

def test_should_reply_on_comment_request():
    # Reply to some user (not bot) but asking to comment
    msg = FakeMessage("Прокоментуй це", user_id=123, message_id=2, reply_to_message_id=1)
    
    # We need to mock is_bot_message to return False for msg 1
    import src.panbot.helpers as helpers_mod
    def mock_is_bot(cid, mid):
        return False
    
    import src.tools.db as db_mod
    original_is_bot = db_mod.is_bot_message
    db_mod.is_bot_message = mock_is_bot
    
    try:
        assert should_reply(msg) is True
    finally:
        db_mod.is_bot_message = original_is_bot

def test_father_respect_injection(monkeypatch):
    import src.tools.handlers as handlers_mod
    
    # Mock PanBotEngine.generate_response to capture traits_block
    captured_traits = []
    async def mock_gen_resp(self, message, quoted_block, traits_block, user_name, user_message, custom_role=None):
        captured_traits.append(traits_block)
        return "mocked response"
    
    from src.panbot.engine.core import PanBotEngine
    monkeypatch.setattr(PanBotEngine, "generate_response", mock_gen_resp)
    
    # Mock DB functions
    monkeypatch.setattr(handlers_mod, "get_panbot_usage", lambda u, c, d: 0)
    monkeypatch.setattr(handlers_mod, "increment_panbot_usage", lambda u, c, d: 1)
    
    import src.panbot.helpers as helpers_mod
    monkeypatch.setattr(helpers_mod, "get_user_traits", lambda u: {})

    import asyncio
    
    # Test for "father" ID
    father_msg = FakeMessage("Привіт, сину", user_id=229953580, message_id=100)
    asyncio.run(handlers_mod.get_panbot_response(father_msg))
    
    assert "твій творець" in captured_traits[0]
    assert "максимальною пошаною" in captured_traits[0]

    # Test for normal user
    normal_msg = FakeMessage("Привіт, бот", user_id=123, message_id=101)
    asyncio.run(handlers_mod.get_panbot_response(normal_msg))
    
    assert "твій творець" not in captured_traits[1]

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

    import asyncio

    # Father sets role
    father_msg = FakeMessage("ботяндра, тепер твоя нова роль - Ти тепер котик", user_id=229953580, message_id=100)
    response = asyncio.run(handlers_mod.get_panbot_response(father_msg))

    assert "Тепер моя роль: Ти тепер котик" in response
    assert roles[1] == "Ти тепер котик"

    # Normal user tries to set role (should be ignored and passed to LLM)
    captured_custom_roles = []
    async def mock_gen_resp(self, message, quoted_block, traits_block, user_name, user_message, custom_role=None):
        captured_custom_roles.append(custom_role)
        return "mocked response"
    
    from src.panbot.engine.core import PanBotEngine
    monkeypatch.setattr(PanBotEngine, "generate_response", mock_gen_resp)
    monkeypatch.setattr(handlers_mod, "get_traits_block", lambda u: "")
    monkeypatch.setattr(handlers_mod, "get_quoted_block", lambda m: "")

    normal_msg = FakeMessage("ботяндра, тепер твоя нова роль - Ти тепер пес", user_id=123, message_id=101)
    asyncio.run(handlers_mod.get_panbot_response(normal_msg))

    # Role should NOT have changed in DB for normal user's message
    # And it should have passed the EXISTING role to LLM
    assert roles[1] == "Ти тепер котик"
    assert captured_custom_roles[0] == "Ти тепер котик"
