"""«save» у відповідь на картинку — картинка в особисті."""
from datetime import datetime, timezone

import pytest
from telegram import Chat, Message, PhotoSize, Update, User
from telegram.error import BadRequest, Forbidden
from telegram.ext import MessageHandler

import src.save.handler as save_mod
import src.tools.config as config

CHAT_ID = -100500
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
CHAT = Chat(CHAT_ID, Chat.SUPERGROUP)
PETRO = User(1, "Петро", False)
PHOTO = Message(message_id=10, date=NOW, chat=CHAT, from_user=User(2, "Оля", False),
                photo=(PhotoSize("fid", "uid", 100, 100),))
TEXT = Message(message_id=11, date=NOW, chat=CHAT, from_user=User(2, "Оля", False), text="привіт")


def update(text, reply_to=PHOTO):
    return Update(1, message=Message(message_id=20, date=NOW, chat=CHAT, from_user=PETRO,
                                     text=text, reply_to_message=reply_to))


# --- що вважається командою ---

@pytest.mark.parametrize("text", ["save", "Save", "SAVE!", "  save.  "])
def test_save_variants_match(text):
    assert MessageHandler(save_mod.SAVE_FILTER, lambda *a: None).check_update(update(text))


@pytest.mark.parametrize("text", ["save me", "збережи це", "please save", "saved"])
def test_other_text_does_not_match(text):
    assert not MessageHandler(save_mod.SAVE_FILTER, lambda *a: None).check_update(update(text))


def test_save_without_reply_does_not_match():
    assert not MessageHandler(save_mod.SAVE_FILTER, lambda *a: None).check_update(update("save", None))


# --- обробка ---

class FakeMsg:
    def __init__(self, reply_to):
        self.from_user = PETRO
        self.reply_to_message = reply_to
        self.replies = []
        self.reactions = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)

    async def set_reaction(self, emoji):
        self.reactions.append(emoji)


class FakeBot:
    username = "fake_panbot"

    def __init__(self, error=None):
        self.error = error
        self.copied = []

    async def copy_message(self, chat_id, from_chat_id, message_id):
        if self.error:
            raise self.error
        self.copied.append((chat_id, from_chat_id, message_id))


def run(msg, bot, chat_id=CHAT_ID):
    upd = type("U", (), {"effective_message": msg, "effective_chat": type("C", (), {"id": chat_id})()})()
    ctx = type("Ctx", (), {"bot": bot})()
    return save_mod.on_save(upd, ctx)


@pytest.fixture(autouse=True)
def known_chat(monkeypatch):
    monkeypatch.setattr(config, "KNOWN_CHAT_IDS", {CHAT_ID})


async def test_picture_sent_to_private_chat():
    msg, bot = FakeMsg(PHOTO), FakeBot()
    await run(msg, bot)
    assert bot.copied == [(PETRO.id, CHAT_ID, PHOTO.message_id)], "в особисті тому, хто написав save"
    assert msg.reactions == ["👍"] and msg.replies == [], "успіх — реакцією, без повідомлень у чат"


async def test_user_who_never_started_bot_gets_link():
    msg = FakeMsg(PHOTO)
    await run(msg, FakeBot(Forbidden("bot can't initiate conversation with a user")))
    assert len(msg.replies) == 1
    assert "https://t.me/fake_panbot?start=save" in msg.replies[0]
    assert msg.reactions == []


async def test_protected_content_explained():
    msg = FakeMsg(PHOTO)
    await run(msg, FakeBot(BadRequest("Message can't be copied")))
    assert msg.replies and "не можна скопіювати" in msg.replies[0]


async def test_save_on_text_message_ignored():
    msg, bot = FakeMsg(TEXT), FakeBot()
    await run(msg, bot)
    assert bot.copied == [] and msg.replies == [] and msg.reactions == []


async def test_unknown_chat_ignored():
    msg, bot = FakeMsg(PHOTO), FakeBot()
    await run(msg, bot, chat_id=-1)
    assert bot.copied == []


async def test_failed_reaction_does_not_break_save():
    class NoReactions(FakeMsg):
        async def set_reaction(self, emoji):
            raise BadRequest("Reactions are disabled")

    msg, bot = NoReactions(PHOTO), FakeBot()
    await run(msg, bot)
    assert bot.copied, "картинку надіслано, хоч реакцію поставити не вийшло"


async def test_start_in_private_explains_usage():
    replies = []

    class Msg:
        async def reply_text(self, text, **kwargs):
            replies.append(text)

    await save_mod.cmd_start(type("U", (), {"effective_message": Msg()})(), None)
    assert "save" in replies[0]


# --- «save» не має запускати відповідь бота ---

@pytest.mark.parametrize("text, asked", [("save", False), ("ботяндра, а ти що скажеш?", True)])
async def test_save_reply_does_not_reach_panbot(monkeypatch, text, asked):
    """Якщо картинку надіслав сам бот, «save» — це ще й відповідь боту."""
    import src.tools.handlers as handlers_mod

    calls = []

    async def fake_db_call(func, *args, **kwargs):
        return None

    async def fake_decision(message):
        calls.append(message)
        return None

    monkeypatch.setattr(config, "KNOWN_CHAT_IDS", {CHAT_ID})
    monkeypatch.setattr(config, "PANBOT_CHAT_IDS", {CHAT_ID})
    monkeypatch.setattr(handlers_mod, "db_call", fake_db_call)
    monkeypatch.setattr(handlers_mod, "schedule_reaction", lambda *a: None)
    monkeypatch.setattr(handlers_mod, "should_reply_with_agent", fake_decision)

    upd = update(text)
    await handlers_mod.on_message(upd, type("Ctx", (), {"application": None})())
    assert bool(calls) is asked


# --- лотерея для людей зі списку ---

@pytest.mark.parametrize("wins, chance", [(0, 0.50), (1, 0.55), (5, 0.75), (10, 1.0), (30, 1.0)])
def test_lottery_chance_grows_by_5_percent_and_caps(wins, chance):
    assert save_mod.lottery_chance(wins) == pytest.approx(chance)


@pytest.fixture
def lottery(monkeypatch):
    """Петро в лотереї; БД — словник перемог."""
    state = {"wins": {}, "roll": 0.0}
    monkeypatch.setattr(config, "SAVE_LOTTERY_USER_IDS", {PETRO.id})

    async def fake_db_call(func, user_id):
        if func.__name__ == "get_save_lottery_wins":
            return state["wins"].get(user_id, 0)
        if func.__name__ == "record_save_lottery_win":
            state["wins"][user_id] = state["wins"].get(user_id, 0) + 1
            return state["wins"][user_id]
        raise AssertionError(func.__name__)

    monkeypatch.setattr(save_mod, "db_call", fake_db_call)
    monkeypatch.setattr(save_mod, "_roll", lambda: state["roll"])
    return state


async def test_lottery_lose_mocks_and_shows_chance(lottery):
    lottery["roll"] = 0.6          # шанс 50% — програш
    msg, bot = FakeMsg(PHOTO), FakeBot()
    await run(msg, bot)

    assert bot.copied == [], "картинку не надіслано"
    assert len(msg.replies) == 1 and "50%" in msg.replies[0]
    assert msg.reactions == []
    assert lottery["wins"] == {}, "програш шанс не змінює"


async def test_lottery_win_sends_and_raises_chance(lottery):
    lottery["roll"] = 0.1
    msg, bot = FakeMsg(PHOTO), FakeBot()
    await run(msg, bot)

    assert bot.copied and msg.reactions == ["👍"]
    assert lottery["wins"] == {PETRO.id: 1}

    lottery["roll"] = 0.52         # тепер шанс 55% — виграш
    await run(FakeMsg(PHOTO), bot)
    assert lottery["wins"] == {PETRO.id: 2}


async def test_lottery_win_not_delivered_does_not_count(lottery):
    """Start не натиснутий — картинка не дійшла, отже й шанс не росте."""
    lottery["roll"] = 0.0
    msg = FakeMsg(PHOTO)
    await run(msg, FakeBot(Forbidden("bot can't initiate conversation with a user")))
    assert lottery["wins"] == {}
    assert "start=save" in msg.replies[0]


async def test_users_outside_lottery_always_get_picture(lottery, monkeypatch):
    monkeypatch.setattr(config, "SAVE_LOTTERY_USER_IDS", {999})
    lottery["roll"] = 0.99
    msg, bot = FakeMsg(PHOTO), FakeBot()
    await run(msg, bot)
    assert bot.copied and lottery["wins"] == {}


async def test_lose_on_text_reply_still_ignored(lottery):
    """Лотерея — лише для «save» на медіа; на текст бот мовчить, як і раніше."""
    lottery["roll"] = 0.99
    msg = FakeMsg(TEXT)
    await run(msg, FakeBot())
    assert msg.replies == []


def test_lose_phrases_all_show_chance():
    for phrase in save_mod.LOTTERY_LOSE_PHRASES:
        assert "{chance}%" in phrase
