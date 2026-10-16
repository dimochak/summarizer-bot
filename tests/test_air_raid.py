"""Гіфка на тривогу й відбій.

Головне — постити лише на переході й ніколи не оголошувати відбій через
збій мережі чи ліміт API.
"""
import httpx
import pytest

import src.alerts.air_raid as air_raid
import src.tools.config as config

CHAT = -100500


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ALERTS_API_TOKEN", "test-alerts-token")
    monkeypatch.setattr(config, "ALERTS_REGION_UID", 31)
    monkeypatch.setattr(config, "ALERTS_REGION_NAME", "м. Київ")
    monkeypatch.setattr(config, "ALERTS_CHAT_IDS", {CHAT})
    monkeypatch.setattr(config, "REACTIONS_DIR", str(tmp_path))
    monkeypatch.setattr(air_raid, "_cache", {"last_modified": None, "status": None})
    monkeypatch.setattr(air_raid, "_file_ids", {})


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

    async def fake_db_call(func, region_uid, active, now_ts):
        assert func.__name__ == "record_air_alert_state"
        previous = store.get(region_uid)
        store[region_uid] = active
        if previous is None:
            return "init"
        return "same" if previous == active else "changed"

    monkeypatch.setattr(air_raid, "db_call", fake_db_call)
    return store


# --- запит до API ---

async def test_request_targets_region_and_sends_token():
    client = client_returning(httpx.Response(200, json="N"))
    assert await air_raid.fetch_status(client) == "N"

    request = client.seen[0]
    assert request.url.path == "/v1/iot/active_air_raid_alerts/31.json"
    assert request.headers["Authorization"] == "Bearer test-alerts-token"
    assert "token" not in str(request.url), "токен не має потрапляти в URL і логи"


async def test_not_modified_reuses_last_status():
    client = client_returning(
        httpx.Response(200, json="A", headers={"Last-Modified": "Fri, 16 Oct 2026 10:00:00 GMT"}),
        httpx.Response(304),
    )
    assert await air_raid.fetch_status(client) == "A"
    assert await air_raid.fetch_status(client) == "A"
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
async def test_failures_mean_unknown_not_all_clear(response):
    assert await air_raid.fetch_status(client_returning(response)) is None


# --- переходи ---

async def run(bot, *statuses):
    client = client_returning(*statuses)
    for _ in statuses:
        await air_raid.check_air_raid(bot, client)


async def test_first_poll_only_records_state(state):
    """Після першого деплою бот не повинен кричати «тривога» на поточний стан."""
    bot = FakeBot()
    await run(bot, httpx.Response(200, json="A"))
    assert bot.sent == []
    assert state == {31: True}


async def test_alert_and_all_clear_posted_once_each(state):
    bot = FakeBot()
    await run(
        bot,
        httpx.Response(200, json="N"),
        httpx.Response(200, json="A"),
        httpx.Response(200, json="A"),
        httpx.Response(200, json="N"),
        httpx.Response(200, json="N"),
    )
    assert [caption for _, _, caption in bot.sent] == [
        "🚨 Повітряна тривога — м. Київ",
        "✅ Відбій тривоги — м. Київ",
    ]


async def test_partial_counts_as_alert(state):
    bot = FakeBot()
    await run(bot, httpx.Response(200, json="N"), httpx.Response(200, json="P"))
    assert len(bot.sent) == 1 and "тривога" in bot.sent[0][2]


async def test_outage_during_alert_does_not_announce_all_clear(state):
    bot = FakeBot()
    await run(
        bot,
        httpx.Response(200, json="N"),
        httpx.Response(200, json="A"),
        httpx.Response(429),
        httpx.ConnectTimeout("timeout"),
        httpx.Response(200, json="A"),
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
    await run(bot, httpx.Response(200, json="N"), httpx.Response(200, json="A"))
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
    "token, chats, expected",
    [("t", {CHAT}, 1), ("", {CHAT}, 0), ("t", set(), 0)],
)
def test_scheduled_only_when_configured(monkeypatch, token, chats, expected):
    monkeypatch.setattr(config, "ALERTS_API_TOKEN", token)
    monkeypatch.setattr(config, "ALERTS_CHAT_IDS", chats)
    app = type("App", (), {"job_queue": FakeJobQueue()})()

    air_raid.schedule_air_raid(app)
    assert len(app.job_queue.jobs) == expected
