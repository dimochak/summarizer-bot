from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.panbot.bot import PanBot, SarcasmLimitExceeded

DAILY_LIMIT = 50
CHAT_ID = -1001234567890


class FakeMessage:
    def __init__(self, text, user_id, message_id, reply_to_message_id=None, date=None, chat_id=CHAT_ID):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id, full_name=f"user{user_id}")
        self.message_id = message_id
        self.reply_to_message_id = reply_to_message_id
        self.date = date or datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.chat = SimpleNamespace(id=chat_id)


@pytest.fixture
def pan_bot():
    return PanBot(daily_limit=DAILY_LIMIT)


# --- should_reply -----------------------------------------------------------

def test_no_trigger_no_reply(pan_bot):
    msg = FakeMessage("random text", user_id=123, message_id=1)
    assert pan_bot.should_reply(msg) is False


@pytest.mark.parametrize("text", ["ботяндра, привіт", "гей ботяндрік що там", "БОТЯНДРА!"])
def test_trigger_word_replies(pan_bot, text):
    msg = FakeMessage(text, user_id=123, message_id=1)
    assert pan_bot.should_reply(msg) is True


def test_non_text_message_never_replies(pan_bot):
    msg = FakeMessage(None, user_id=123, message_id=1)
    assert pan_bot.should_reply(msg) is False


# --- build_conversation_prompt ---------------------------------------------

def test_conversation_prompt_includes_past_messages(pan_bot):
    rows = [
        {"text": "що це таке?", "full_name": "Оля", "username": None},
        {"text": "Поясни, будь-ласка", "full_name": None, "username": "petro"},
    ]
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = rows
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cur

    reply = FakeMessage("І навіщо це все?", user_id=21, message_id=3, reply_to_message_id=2)
    with patch("src.panbot.bot.db", return_value=fake_conn):
        prompt = pan_bot.build_conversation_prompt(reply)

    assert "що це таке?" in prompt
    assert "Поясни, будь-ласка" in prompt
    # The current message itself is excluded by the SQL query, not the prompt builder,
    # so we only assert the fetched context rows are rendered.
    assert "Оля" in prompt


# --- process_reply quota ----------------------------------------------------

@pytest.mark.asyncio
async def test_daily_limit_enforced(pan_bot):
    reply = FakeMessage("ботяндра, круто?", user_id=555, message_id=10)
    with patch("src.panbot.bot.get_panbot_usage", return_value=DAILY_LIMIT):
        with pytest.raises(SarcasmLimitExceeded):
            await pan_bot.process_reply(reply)


@pytest.mark.asyncio
async def test_under_limit_returns_response(pan_bot):
    reply = FakeMessage("ботяндра, тепер що?", user_id=99, message_id=11)
    with patch("src.panbot.bot.get_panbot_usage", return_value=0), \
         patch("src.panbot.bot.increment_panbot_usage", return_value=1), \
         patch.object(PanBot, "_generate_sarcastic_response", new=AsyncMock(return_value="сарказм")):
        result = await pan_bot.process_reply(reply)
    assert "сарказм" in result


@pytest.mark.asyncio
async def test_limit_is_configurable():
    pb = PanBot(daily_limit=1)
    reply = FakeMessage("ботяндра, дратуєш", user_id=7, message_id=1)
    with patch("src.panbot.bot.get_panbot_usage", return_value=1):
        with pytest.raises(SarcasmLimitExceeded):
            await pb.process_reply(reply)
