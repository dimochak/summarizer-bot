"""Тести інструментів агента."""
import httpx
import pytest

from src.agent.tools.weather import get_weather
from src.agent.tools import build_tools


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return self._responses.pop(0)


GEO_KYIV = {"results": [{"name": "Київ", "country": "Україна", "latitude": 50.45, "longitude": 30.52}]}
FORECAST = {"current": {
    "temperature_2m": 17.6, "apparent_temperature": 15.2,
    "weather_code": 61, "wind_speed_10m": 12.4,
}}


@pytest.fixture
def fake_http(monkeypatch):
    def install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
        return client

    return install


async def test_weather_formats_readable_answer(fake_http):
    fake_http([FakeResponse(GEO_KYIV), FakeResponse(FORECAST)])

    result = await get_weather.ainvoke({"city": "Київ"})

    assert "Київ, Україна" in result
    assert "18°C" in result          # округлення 17.6
    assert "невеликий дощ" in result  # код 61
    assert "12 км/год" in result


async def test_unknown_city_says_so(fake_http):
    fake_http([FakeResponse({"results": []})])
    assert "не знайдено" in await get_weather.ainvoke({"city": "Нідеїсько"})


async def test_network_failure_is_not_fatal(monkeypatch):
    """Збій зовнішнього сервісу має стати повідомленням, а не винятком."""
    class Broken:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def get(self, *a, **kw): raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: Broken())
    result = await get_weather.ainvoke({"city": "Київ"})
    assert "Не вдалося" in result


async def test_unknown_weather_code_does_not_crash(fake_http):
    fake_http([
        FakeResponse(GEO_KYIV),
        FakeResponse({"current": {
            "temperature_2m": 1.0, "apparent_temperature": 1.0,
            "weather_code": 12345, "wind_speed_10m": 1.0,
        }}),
    ])
    assert "невизначено" in await get_weather.ainvoke({"city": "Київ"})


async def test_search_tool_reports_empty_result(monkeypatch):
    import src.agent.tools.chat as chat_mod

    async def mock_db_call(func, *args, **kwargs):
        return []

    monkeypatch.setattr(chat_mod, "db_call", mock_db_call)

    search = next(t for t in build_tools(7) if t.name == "search_chat_history")
    result = await search.ainvoke({"query": "переїзд"})
    assert "нічого не знайдено" in result


async def test_search_tool_formats_hits(monkeypatch):
    import src.agent.tools.chat as chat_mod

    captured = {}

    async def mock_db_call(func, chat_id, query, since, limit):
        captured.update(chat_id=chat_id, query=query, limit=limit)
        return [{
            "text": "обговорювали переїзд у Львів",
            "full_name": "Петро", "username": "petro",
            "ts_utc": 1700000000, "user_id": 1, "message_id": 5,
        }]

    monkeypatch.setattr(chat_mod, "db_call", mock_db_call)

    search = next(t for t in build_tools(7) if t.name == "search_chat_history")
    result = await search.ainvoke({"query": "переїзд"})

    assert "Петро" in result
    assert "переїзд у Львів" in result
    # chat_id узятий із замикання, а не з аргументів моделі
    assert captured["chat_id"] == 7


async def test_summarize_tool_clamps_absurd_amounts(monkeypatch):
    import src.agent.tools.chat as chat_mod

    captured = {}

    async def mock_get_summary(chat_id, request_type, value):
        captured.update(chat_id=chat_id, request_type=request_type, value=value)
        return "підсумок"

    monkeypatch.setattr(chat_mod._summary_engine, "get_summary", mock_get_summary)

    summarize = next(t for t in build_tools(7) if t.name == "summarize_chat")
    await summarize.ainvoke({"period": "hours", "amount": 9999})

    assert captured["value"] == 48, "нереальний відрізок мав обрізатись"
    assert captured["chat_id"] == 7
