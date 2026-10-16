"""Глобальний обробник помилок PTB."""
from psycopg_pool import PoolTimeout

import src.main as main_mod
import src.tools.config as config


class FakeLog:
    def __init__(self):
        self.lines = []
        self.with_traceback = []

    def error(self, message):
        self.lines.append(message)

    def opt(self, exception=None):
        self.with_traceback.append(exception)
        return self


def _update(chat_id):
    return type("U", (), {"effective_chat": type("C", (), {"id": chat_id})()})()


async def test_db_outage_is_one_line_without_traceback(monkeypatch):
    log = FakeLog()
    monkeypatch.setattr(config, "log", log)
    context = type("Ctx", (), {"error": PoolTimeout("couldn't get a connection")})()

    await main_mod.on_error(_update(-100), context)

    assert log.lines == ["DB unavailable (chat -100): couldn't get a connection"]
    assert log.with_traceback == []


async def test_unexpected_error_keeps_traceback(monkeypatch):
    log = FakeLog()
    monkeypatch.setattr(config, "log", log)
    error = ValueError("boom")
    context = type("Ctx", (), {"error": error})()

    await main_mod.on_error(None, context)

    assert log.with_traceback == [error]
    assert "chat None" in log.lines[0]
