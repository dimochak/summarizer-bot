import pytest
from src.panbot.bot import PanBot, SarcasmLimitExceeded

class FakeMessage:
    def __init__(self, text, user_id, message_id, reply_to_message_id=None, date=None):
        self.text = text
        self.from_user = type("User", (), {"id": user_id})
        self.message_id = message_id
        self.reply_to_message_id = reply_to_message_id
        self.date = date


DAILY_LIMIT = 50


@pytest.fixture
def pan_bot():
    return PanBot(daily_limit=DAILY_LIMIT)


def test_no_trigger_no_reply(pan_bot):
    msg = FakeMessage("random text", user_id=123, message_id=1)
    assert pan_bot.should_reply(msg) is False


def test_trigger_and_reply(pan_bot):
    msg = FakeMessage("Пан бот, привіт", user_id=123, message_id=1)
    assert pan_bot.should_reply(msg) is True


def test_conversation_memory_prompt_includes_past_messages(pan_bot):
    # User triggers bot → then continues conversation in replies
    # orig = FakeMessage("Пан бот, що це таке?", user_id=20, message_id=1)
    # reply1 = FakeMessage("Поясни, будь-ласка", user_id=21, message_id=2, reply_to_message_id=1)
    reply2 = FakeMessage("І навіщо це все?", user_id=21, message_id=3, reply_to_message_id=2)
    # The context for reply2 should include orig and reply1 in conversational order
    prompt = pan_bot.build_conversation_prompt(reply2)
    assert "що це таке?" in prompt
    assert "Поясни, будь-ласка" in prompt
    assert "І навіщо це все?" not in prompt


def test_daily_limit_enforced(pan_bot):
    reply = FakeMessage("Пан бот, круто?", user_id=555, message_id=10, reply_to_message_id=1)
    pan_bot._reset_limits_today()
    for _ in range(DAILY_LIMIT):
        pan_bot.process_reply(reply)
    with pytest.raises(SarcasmLimitExceeded):
        pan_bot.process_reply(reply)


def test_limit_over_message(pan_bot):
    pan_bot._reset_limits_today()
    reply = FakeMessage("Пан бот, тепер що?", user_id=99, message_id=11, reply_to_message_id=4)
    for _ in range(DAILY_LIMIT):
        pan_bot.process_reply(reply)
    with pytest.raises(SarcasmLimitExceeded) as excinfo:
        pan_bot.process_reply(reply)
    assert "обмеження" in str(excinfo.value) or "limit" in str(excinfo.value)


def test_limit_is_configurable():
    pb = PanBot(daily_limit=1)
    fake = FakeMessage("Пан бот, дратуєш", user_id=7, message_id=1, reply_to_message_id=123)
    pb._reset_limits_today()
    pb.process_reply(fake)
    with pytest.raises(SarcasmLimitExceeded):
        pb.process_reply(fake)


def test_memory_persists_per_user():
    pb = PanBot(daily_limit=10)
    pb._reset_limits_today()
    msg1 = FakeMessage("Пан бот, поясни", 42, 1, reply_to_message_id=99)
    msg2 = FakeMessage("Пан бот, ще раз", 43, 2, reply_to_message_id=1)
    pb.process_reply(msg1)
    pb.process_reply(msg2)
    assert pb.get_conversation_history(42) == [msg1.text]
    assert pb.get_conversation_history(43) == [msg2.text]
