"""Гіфка на тривогу й відбій.

Головне — постити лише на переході й ніколи не оголошувати відбій через
збій мережі чи ліміт API.
"""
from time import time

import httpx
import pytest

import src.alerts.air_raid as air_raid
import src.tools.config as config

CHAT = -100500


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ALERTS_SOURCE", "jaam")
    monkeypatch.setattr(config, "ALERTS_JAAM_REGION", "Київ")
    monkeypatch.setattr(config, "ALERTS_API_TOKEN", "test-alerts-token")
    monkeypatch.setattr(config, "ALERTS_REGION_UID", 31)
    monkeypatch.setattr(config, "ALERTS_REGION_NAME", "м. Київ")
    monkeypatch.setattr(config, "ALERTS_CHAT_IDS", {CHAT})
    monkeypatch.setattr(config, "REACTIONS_DIR", str(tmp_path))
    monkeypatch.setattr(
        air_raid, "_cache", {"last_modified": None, "status": None, "backoff_until": 0.0}
    )
    monkeypatch.setattr(air_raid, "_file_ids", {})


def jaam(alertnow, district=False, changes=None, region="Київ"):
    """Відповідь JAAM v2 у тому вигляді, який сервер віддає насправді."""
    return httpx.Response(200, json={
        "version": 2,
        "states": {
            region: {"alertnow": alertnow, "district": district, "changes": changes},
            "Київська область": {"alertnow": True, "district": True,
                                 "changes": "2026-10-16T16:13:38Z"},
        },
    })


def client_returning(*responses):
    """HTTP-клієнт, що по черзі віддає задані відповіді й запамʼятовує запити."""
    queue = list(responses)
    seen = []

    def handler(request):
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.seen = seen
    return client


class FakeBot:
    def __init__(self):
        self.sent = []
        self.uploads = 0

    def _media(self, value):
        if isinstance(value, str):
            return value
        self.uploads += 1
        return f"file-id-{self.uploads}"

    async def send_message(self, chat_id, text):
        self.sent.append(("text", chat_id, text))

    async def send_animation(self, chat_id, animation, caption):
        self.sent.append(("animation", chat_id, caption))
        file_id = self._media(animation)
        return type("Msg", (), {"animation": type("A", (), {"file_id": file_id})()})()

    async def send_photo(self, chat_id, photo, caption):
        self.sent.append(("photo", chat_id, caption))
        file_id = self._media(photo)
        return type("Msg", (), {"animation": None, "video": None, "document": None,
                                "photo": [type("P", (), {"file_id": file_id})()]})()


@pytest.fixture
def state(monkeypatch):
    """Стан у БД замінено словником з тією ж семантикою, що й record_air_alert_state."""
    store = {}

    async def fake_db_call(func, region, active, now_ts):
        assert func.__name__ == "record_air_alert_state"
        previous = store.get(region)
        store[region] = active
        if previous is None:
            return "init"
        return "same" if previous == active else "changed"

    monkeypatch.setattr(air_raid, "db_call", fake_db_call)
    return store


# --- JAAM ---

async def test_jaam_reads_v2_region_without_token():
    client = client_returning(jaam(True, district=True, changes="2026-10-16T16:13:38Z"))
    status = await air_raid.fetch_status(client)

    assert status.active is True and status.partial is True
    assert status.changed_at.isoformat() == "2026-10-16T16:13:38+00:00"
    request = client.seen[0]
    assert str(request.url) == "https://jaam.net.ua/alerts_statuses_v2.json"
    assert "Authorization" not in request.headers
    assert "summarizer-bot" in request.headers["User-Agent"]


async def test_jaam_city_and_oblast_are_different_keys():
    """Тривога в області не означає тривоги в місті."""
    status = await air_raid.fetch_status(client_returning(jaam(False)))
    assert status.active is False


async def test_jaam_unknown_region_is_unknown_not_all_clear(monkeypatch):
    monkeypatch.setattr(config, "ALERTS_JAAM_REGION", "Киев")
    assert await air_raid.fetch_status(client_returning(jaam(True))) is None


@pytest.mark.parametrize(
    "body",
    [{"version": 2}, {"states": []}, {"states": {"Київ": {"alertnow": "yes"}}}, ["Київ"]],
)
async def test_jaam_malformed_payload_is_unknown(body):
    response = httpx.Response(200, json=body)
    assert await air_raid.fetch_status(client_returning(response)) is None


async def test_rate_limit_pauses_polling():
    """Після 429 не долбимо сервер ще хвилину — інакше бан IP."""
    client = client_returning(httpx.Response(429), jaam(True))
    assert await air_raid.fetch_status(client) is None
    assert await air_raid.fetch_status(client) is None
    assert len(client.seen) == 1, "друге опитування в межах паузи не має йти в мережу"


async def test_rate_limit_pause_expires():
    air_raid._cache["backoff_until"] = time() - 1
    status = await air_raid.fetch_status(client_returning(jaam(True)))
    assert status.active is True


# --- alerts.in.ua ---

@pytest.fixture
def alerts_in_ua(monkeypatch):
    monkeypatch.setattr(config, "ALERTS_SOURCE", "alerts_in_ua")


async def test_request_targets_region_and_sends_token(alerts_in_ua):
    client = client_returning(httpx.Response(200, json="N"))
    assert (await air_raid.fetch_status(client)).active is False

    request = client.seen[0]
    assert request.url.path == "/v1/iot/active_air_raid_alerts/31.json"
    assert request.headers["Authorization"] == "Bearer test-alerts-token"
    assert "token" not in str(request.url), "токен не має потрапляти в URL і логи"


async def test_not_modified_reuses_last_status(alerts_in_ua):
    client = client_returning(
        httpx.Response(200, json="A", headers={"Last-Modified": "Fri, 16 Oct 2026 10:00:00 GMT"}),
        httpx.Response(304),
    )
    assert (await air_raid.fetch_status(client)).active is True
    assert (await air_raid.fetch_status(client)).active is True
    assert client.seen[1].headers["If-Modified-Since"] == "Fri, 16 Oct 2026 10:00:00 GMT"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="Too many requests"),
        httpx.Response(500),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json="X"),
        httpx.ConnectTimeout("timeout"),
    ],
)
@pytest.mark.parametrize("source", ["jaam", "alerts_in_ua"])
async def test_failures_mean_unknown_not_all_clear(monkeypatch, source, response):
    monkeypatch.setattr(config, "ALERTS_SOURCE", source)
    assert await air_raid.fetch_status(client_returning(response)) is None


# --- переходи ---

async def run(bot, *statuses):
    client = client_returning(*statuses)
    for _ in statuses:
        await air_raid.check_air_raid(bot, client)


async def test_first_poll_only_records_state(state):
    """Після першого деплою бот не повинен кричати «тривога» на поточний стан."""
    bot = FakeBot()
    await run(bot, jaam(True))
    assert bot.sent == []
    assert state == {"jaam:Київ": True}


async def test_alert_and_all_clear_posted_once_each(state):
    bot = FakeBot()
    await run(bot, jaam(False), jaam(True), jaam(True), jaam(False), jaam(False))
    assert [caption for _, _, caption in bot.sent] == [
        "🚨 Повітряна тривога — м. Київ",
        "✅ Відбій тривоги — м. Київ",
    ]


async def test_partial_counts_as_alert(state, alerts_in_ua):
    bot = FakeBot()
    await run(bot, httpx.Response(200, json="N"), httpx.Response(200, json="P"))
    assert len(bot.sent) == 1 and "тривога" in bot.sent[0][2]


async def test_jaam_partial_counts_as_alert(state):
    bot = FakeBot()
    await run(bot, jaam(False), jaam(True, district=True))
    assert len(bot.sent) == 1 and "тривога" in bot.sent[0][2]


async def test_state_key_includes_source(state):
    """Зміна джерела починає з «init», а не з хибного переходу."""
    await run(FakeBot(), jaam(True))
    assert state == {"jaam:Київ": True}


async def test_lag_from_source_is_logged(state, monkeypatch):
    lines = []
    fake_log = type("Log", (), {
        "info": staticmethod(lines.append),
        "warning": staticmethod(lines.append),
        "exception": staticmethod(lines.append),
    })
    monkeypatch.setattr(config, "log", fake_log)

    await run(FakeBot(), jaam(False), jaam(True, changes="2026-10-16T16:13:38Z"))
    assert any("відставання" in line and "jaam" in line for line in lines)


async def test_outage_during_alert_does_not_announce_all_clear(state):
    bot = FakeBot()
    await run(
        bot,
        jaam(False),
        jaam(True),
        httpx.ConnectTimeout("timeout"),
        httpx.Response(502),
        httpx.Response(429),
    )
    assert len(bot.sent) == 1, "лише тривога; збої — не відбій"


async def test_send_failure_in_one_chat_does_not_block_others(state, monkeypatch):
    monkeypatch.setattr(config, "ALERTS_CHAT_IDS", {1, 2})

    class FlakyBot(FakeBot):
        async def send_message(self, chat_id, text):
            if chat_id == 1:
                raise RuntimeError("бота вигнали з чату")
            await super().send_message(chat_id, text)

    bot = FlakyBot()
    await run(bot, jaam(False), jaam(True))
    assert [chat for _, chat, _ in bot.sent] == [2]


# --- медіа ---

async def test_gif_from_folder_with_caption(tmp_path):
    folder = tmp_path / "air_alert"
    folder.mkdir()
    (folder / "siren.mp4").write_bytes(b"mp4")
    bot = FakeBot()

    await air_raid.announce(bot, CHAT, active=True)
    assert bot.sent == [("animation", CHAT, "🚨 Повітряна тривога — м. Київ")]


async def test_all_clear_uses_its_own_folder(tmp_path):
    (tmp_path / "air_alert").mkdir()
    (tmp_path / "air_alert" / "siren.gif").write_bytes(b"gif")
    (tmp_path / "all_clear").mkdir()
    (tmp_path / "all_clear" / "relief.jpg").write_bytes(b"jpg")
    bot = FakeBot()

    await air_raid.announce(bot, CHAT, active=False)
    assert bot.sent == [("photo", CHAT, "✅ Відбій тривоги — м. Київ")]


async def test_gif_uploaded_once_then_reused_by_file_id(tmp_path):
    """Завантаження гіфки — секунди; другий раз шлемо за file_id миттєво."""
    folder = tmp_path / "air_alert"
    folder.mkdir()
    (folder / "siren.mp4").write_bytes(b"mp4")
    bot = FakeBot()

    await air_raid.announce(bot, 1, active=True)
    await air_raid.announce(bot, 2, active=True)
    assert bot.uploads == 1


async def test_stale_file_id_falls_back_to_upload(tmp_path):
    folder = tmp_path / "air_alert"
    folder.mkdir()
    gif = folder / "siren.gif"
    gif.write_bytes(b"gif")
    air_raid._file_ids[gif] = "expired"

    class PickyBot(FakeBot):
        async def send_animation(self, chat_id, animation, caption):
            if animation == "expired":
                raise RuntimeError("wrong file identifier")
            return await super().send_animation(chat_id, animation, caption)

    bot = PickyBot()
    await air_raid.announce(bot, CHAT, active=True)
    assert bot.uploads == 1
    assert air_raid._file_ids[gif] == "file-id-1"


async def test_no_gifs_falls_back_to_text():
    bot = FakeBot()
    await air_raid.announce(bot, CHAT, active=True)
    assert bot.sent == [("text", CHAT, "🚨 Повітряна тривога — м. Київ")]


# --- розклад ---

class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_repeating(self, callback, interval, first, name):
        self.jobs.append((name, interval))


@pytest.mark.parametrize(
    "source, token, chats, expected",
    [
        ("jaam", "", {CHAT}, 1),             # JAAM токена не потребує
        ("jaam", "", set(), 0),              # без чатів — вимкнено
        ("alerts_in_ua", "t", {CHAT}, 1),
        ("alerts_in_ua", "", {CHAT}, 0),     # alerts.in.ua без токена не працює
        ("typo", "t", {CHAT}, 0),            # невідоме джерело — вимкнено, а не падіння
    ],
)
def test_scheduled_only_when_configured(monkeypatch, source, token, chats, expected):
    monkeypatch.setattr(config, "ALERTS_SOURCE", source)
    monkeypatch.setattr(config, "ALERTS_API_TOKEN", token)
    monkeypatch.setattr(config, "ALERTS_CHAT_IDS", chats)
    app = type("App", (), {"job_queue": FakeJobQueue()})()

    air_raid.schedule_air_raid(app)
    assert len(app.job_queue.jobs) == expected
