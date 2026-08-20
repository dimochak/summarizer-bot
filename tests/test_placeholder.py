"""Тести плейсхолдера «шукаю…».

Головне — він має зʼявлятись ЛИШЕ на повільних відповідях. Якщо слати його
завжди, чат засмічується зайвим повідомленням на кожну репліку.
"""
import asyncio

import pytest

import src.tools.handlers as handlers_mod


class FakePlaceholder:
    def __init__(self, text):
        self.text = text
        self.message_id = 555
        self.edited = None
        self.date = None

    async def edit_text(self, text, **kwargs):
        self.edited = text
        return self


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.message_id = 1

    async def reply_text(self, text, **kwargs):
        p = FakePlaceholder(text)
        self.replies.append(p)
        return p


@pytest.fixture
def fast_placeholder(monkeypatch):
    monkeypatch.setattr(handlers_mod, "PLACEHOLDER_AFTER_SECONDS", 0.05)


async def test_no_placeholder_for_fast_reply(fast_placeholder, monkeypatch):
    async def quick(_):
        return "швидка відповідь"

    monkeypatch.setattr(handlers_mod, "get_panbot_response", quick)

    msg = FakeMessage()
    response, placeholder = await handlers_mod._respond_with_progress(msg)

    assert response == "швидка відповідь"
    assert placeholder is None
    assert msg.replies == [], "на швидку відповідь плейсхолдер не потрібен"


async def test_placeholder_appears_for_slow_reply(fast_placeholder, monkeypatch):
    async def slow(_):
        await asyncio.sleep(0.2)
        return "довга відповідь"

    monkeypatch.setattr(handlers_mod, "get_panbot_response", slow)

    msg = FakeMessage()
    response, placeholder = await handlers_mod._respond_with_progress(msg)

    assert response == "довга відповідь"
    assert placeholder is not None
    assert placeholder.text in handlers_mod.THINKING_PLACEHOLDERS


async def test_slow_reply_edits_placeholder_instead_of_new_message(fast_placeholder, monkeypatch):
    async def slow(_):
        await asyncio.sleep(0.2)
        return "готово"

    monkeypatch.setattr(handlers_mod, "get_panbot_response", slow)

    msg = FakeMessage()
    _, placeholder = await handlers_mod._respond_with_progress(msg)
    await handlers_mod._send_or_edit(msg, placeholder, "готово")

    assert placeholder.edited == "готово"
    assert len(msg.replies) == 1, "відповідь мала замінити плейсхолдер, а не додатись"


async def test_send_or_edit_without_placeholder_sends_new(fast_placeholder):
    msg = FakeMessage()
    result = await handlers_mod._send_or_edit(msg, None, "текст")

    assert result.text == "текст"
    assert len(msg.replies) == 1


async def test_failure_to_send_placeholder_does_not_lose_the_answer(fast_placeholder, monkeypatch):
    """Телеграм може не дати надіслати плейсхолдер — відповідь від цього не зникає."""
    async def slow(_):
        await asyncio.sleep(0.2)
        return "відповідь уціліла"

    class BrokenMessage(FakeMessage):
        async def reply_text(self, text, **kwargs):
            raise RuntimeError("telegram недоступний")

    monkeypatch.setattr(handlers_mod, "get_panbot_response", slow)

    response, placeholder = await handlers_mod._respond_with_progress(BrokenMessage())

    assert response == "відповідь уціліла"
    assert placeholder is None


async def test_exception_propagates_through_progress(fast_placeholder, monkeypatch):
    """Ліміт сарказму має долітати до хендлера, а не губитись у задачі."""
    from src.panbot.exceptions import SarcasmLimitExceeded

    async def over_limit(_):
        raise SarcasmLimitExceeded("досить")

    monkeypatch.setattr(handlers_mod, "get_panbot_response", over_limit)

    with pytest.raises(SarcasmLimitExceeded):
        await handlers_mod._respond_with_progress(FakeMessage())
