"""Детектор баянів: автор фото має доходити і до запису, і до перевірки.

Сама логіка «самоповтор — не баян» живе в SQL і перевіряється інтеграційним
тестом проти справжньої Postgres. Тут — що хендлер передає їй автора.
"""
from datetime import datetime, timezone

import src.tools.config as config
import src.tools.handlers as handlers_mod


class FakePhoto:
    file_unique_id = "pic-1"


class FakeMessage:
    def __init__(self, user_id):
        self.message_id = 42
        self.date = datetime.now(timezone.utc)
        self.photo = [FakePhoto()]
        self.document = None
        self.from_user = type("U", (), {"id": user_id, "full_name": "Петро"})()
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def make_update(msg, chat_id):
    chat = type("Chat", (), {"id": chat_id, "username": None, "title": "t"})()
    return type("Update", (), {"message": msg, "channel_post": None, "effective_chat": chat})()


async def test_photo_author_is_passed_to_storage_and_check(monkeypatch):
    calls = {}

    async def recording_db_call(func, *args, **kwargs):
        calls[func.__name__] = kwargs
        return None  # дубліката немає

    monkeypatch.setattr(handlers_mod, "db_call", recording_db_call)
    monkeypatch.setattr(config, "PANBOT_CHAT_IDS", {-100})

    msg = FakeMessage(user_id=777)
    await handlers_mod.on_photo(make_update(msg, -100), context=None)

    assert calls["upsert_photo_message"]["user_id"] == 777
    assert calls["get_duplicate_photo_message_id"]["user_id"] == 777
    assert msg.replies == [], "дубліката немає — бот має мовчати"


async def test_photo_without_author_still_works(monkeypatch):
    """Пости від імені каналу чи анонімного адміна приходять без from_user."""
    calls = {}

    async def recording_db_call(func, *args, **kwargs):
        calls[func.__name__] = kwargs
        return None

    monkeypatch.setattr(handlers_mod, "db_call", recording_db_call)
    monkeypatch.setattr(config, "PANBOT_CHAT_IDS", {-100})

    msg = FakeMessage(user_id=0)
    msg.from_user = None
    await handlers_mod.on_photo(make_update(msg, -100), context=None)

    assert calls["upsert_photo_message"]["user_id"] is None
    assert calls["get_duplicate_photo_message_id"]["user_id"] is None
