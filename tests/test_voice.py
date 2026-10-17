"""Голосові й кружечки: розшифровка через Gemini і токсична відповідь.

Головне тут:
- дешеві перевірки (тривалість, денний ліміт) відсікають ДО завантаження й моделі;
- технічний збій ліміт не зʼїдає;
- розшифровка лягає в історію один раз — у самому голосовому, а не ще й у відповіді бота;
- текст користувача екранується, бо відповідь іде в Telegram як HTML.
"""
import base64
import random
from datetime import datetime, timezone

import pytest

import src.tools.config as config
import src.voice.analyze as analyze_mod
import src.voice.reply as reply_mod
from src.voice.analyze import VoiceAnalysis
from src.voice.render import render, uk_duration

CHAT = -100700
LIMIT = 5


def context(**overrides):
    ctx = {
        "media": "voice",
        "user_name": "Петро",
        "duration": 42,
        "forwarded": False,
        "is_creator": False,
        "limit": LIMIT,
    }
    ctx.update(overrides)
    return ctx


def phrases(template: str, n: int = 300, **overrides) -> set[str]:
    """Усі фрази, які шаблон здатен видати (з високою ймовірністю)."""
    return {render(template, **context(**overrides)) for _ in range(n)}


@pytest.fixture
def first_phrase(monkeypatch):
    """Фільтр `random` у Jinja бере random.choice — робимо його передбачуваним."""
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])


# --- тривалість словами ---

@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0 секунд"),
        (1, "1 секунда"),
        (2, "2 секунди"),
        (5, "5 секунд"),
        (11, "11 секунд"),
        (14, "14 секунд"),
        (21, "21 секунда"),
        (42, "42 секунди"),
        (60, "1 хвилина"),
        (125, "2 хвилини 5 секунд"),
        (300, "5 хвилин"),
        (None, "0 секунд"),
    ],
)
def test_uk_duration(seconds, expected):
    assert uk_duration(seconds) == expected


# --- шаблони ---

INTRO_TEMPLATES = ["speech.j2", "snoring.j2", "noise.j2", "too_long.j2", "limit.j2", "failed.j2"]


@pytest.mark.parametrize("template", INTRO_TEMPLATES)
@pytest.mark.parametrize("media", ["voice", "video_note"])
@pytest.mark.parametrize("forwarded", [False, True])
@pytest.mark.parametrize("is_creator", [False, True])
def test_every_template_renders_in_every_situation(template, media, forwarded, is_creator):
    for text in phrases(template, n=30, media=media, forwarded=forwarded, is_creator=is_creator):
        assert text
        assert "{" not in text and "}" not in text


@pytest.mark.parametrize("template", ["speech.j2", "snoring.j2"])
def test_phrase_is_random(template):
    assert len(phrases(template)) > 1


@pytest.mark.parametrize("template", ["speech.j2", "snoring.j2", "noise.j2"])
def test_forwarded_mocks_the_forwarder_with_own_phrases(template):
    """Переслане чуже голосове — окремий привід поглузувати, а не звичайна фраза."""
    assert phrases(template, forwarded=True).isdisjoint(phrases(template))


@pytest.mark.parametrize("template", INTRO_TEMPLATES)
def test_creator_is_addressed_with_respect(template):
    """Як і в PanBot: Творцеві — без сарказму."""
    for text in phrases(template, n=30, is_creator=True, forwarded=True):
        assert "батьку" in text.lower()


def test_video_note_has_own_phrases():
    assert phrases("speech.j2", media="video_note").isdisjoint(phrases("speech.j2"))


def test_user_name_is_escaped_in_intro():
    for text in phrases("speech.j2", forwarded=True, user_name="<b>Петро & Ко</b>"):
        assert "<b>Петро" not in text


def test_transcript_block_escapes_user_text():
    text = render(
        "transcript.j2",
        transcript="<script>alert(1)</script> & <b>жирний</b>",
        truncated=False,
        **context(user_name="<i>Петро</i>"),
    )
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "&lt;b&gt;жирний" in text
    assert "&lt;i&gt;Петро" in text
    assert "<blockquote expandable>" in text and "</blockquote>" in text
    assert text.startswith("🎙")


def test_transcript_block_for_video_note_and_truncation():
    text = render("transcript.j2", transcript="текст", truncated=True, **context(media="video_note"))
    assert text.startswith("📹")
    assert "набридло" in text


def test_truncate_respects_telegram_limit():
    short, cut = reply_mod.truncate("коротко")
    assert (short, cut) == ("коротко", False)

    long_text, cut = reply_mod.truncate("слово " * 2000)
    assert cut is True
    assert len(long_text) <= reply_mod.TRANSCRIPT_LIMIT + 1
    assert long_text.endswith("…")


# --- виклик Gemini ---

def test_message_carries_audio_for_voice_and_video_for_video_note():
    voice = analyze_mod.build_message(b"OggS-data", "audio/ogg", "voice").content
    assert voice[1]["type"] == "audio"
    assert voice[1]["mime_type"] == "audio/ogg"
    assert base64.b64decode(voice[1]["base64"]) == b"OggS-data"
    assert "голосове" in voice[0]["text"]

    video = analyze_mod.build_message(b"mp4-data", "video/mp4", "video_note").content
    assert video[1]["type"] == "video"
    assert "кружечок" in video[0]["text"]


@pytest.mark.parametrize(
    "media, mime",
    [("voice", "audio/ogg"), ("video_note", "video/mp4")],
)
def test_gemini_adapter_turns_our_block_into_inline_media(media, mime):
    """Формат блоку — не наша вигадка: адаптер Gemini справді шле його як медіа."""
    from langchain_google_genai.chat_models import _convert_to_parts

    parts = _convert_to_parts(analyze_mod.build_message(b"payload", mime, media).content)
    inline = [p.inline_data for p in parts if p.inline_data]
    assert len(inline) == 1
    assert inline[0].mime_type == mime
    assert inline[0].data == b"payload"


class FakeStructuredLLM:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.result


@pytest.fixture
def fake_llm(monkeypatch):
    holder = {}

    def factory(schema, chat_id=None, purpose="chat", provider=None):
        holder["purpose"] = purpose
        return holder["llm"]

    monkeypatch.setattr(analyze_mod, "get_structured_llm", factory)
    return holder


@pytest.mark.parametrize(
    "raw, expected",
    [
        (VoiceAnalysis(kind="speech", transcript="  привіт усім \n"), VoiceAnalysis(kind="speech", transcript="привіт усім")),
        # Модель «розшифрувала» хропіння — віримо класифікації, а не тексту.
        (VoiceAnalysis(kind="snoring", transcript="хр-р-р"), VoiceAnalysis(kind="snoring")),
        (VoiceAnalysis(kind="other", transcript="Дякую за перегляд"), VoiceAnalysis(kind="other")),
        # Мовлення без жодного слова — не мовлення.
        (VoiceAnalysis(kind="speech", transcript="   "), VoiceAnalysis(kind="other")),
    ],
)
async def test_analyze_voice_normalises_model_output(fake_llm, raw, expected):
    fake_llm["llm"] = FakeStructuredLLM(raw)

    assert await analyze_mod.analyze_voice(b"x", "audio/ogg", "voice", chat_id=CHAT) == expected
    assert fake_llm["purpose"] == "voice"


# --- повний потік ---

class FakeFile:
    async def download_as_bytearray(self):
        return bytearray(b"OggS")


class FakeMedia:
    def __init__(self, duration=12, file_size=20_000, mime_type="audio/ogg"):
        self.duration = duration
        self.file_size = file_size
        self.mime_type = mime_type
        self.downloads = 0

    async def get_file(self):
        self.downloads += 1
        return FakeFile()


class FakeChat:
    def __init__(self, fail_action=False):
        self.id = CHAT
        self.fail_action = fail_action
        self.actions = []

    async def send_action(self, action, message_thread_id=None):
        if self.fail_action:
            raise RuntimeError("Telegram не в настрої")
        self.actions.append(action)


class FakeMessage:
    def __init__(self, kind="voice", forwarded=False, user_id=1, fail_action=False, **media_kwargs):
        self.message_id = 7
        self.date = datetime.now(timezone.utc)
        self.chat = FakeChat(fail_action=fail_action)
        self.from_user = type("U", (), {"id": user_id, "full_name": "Петро", "username": "petro"})()
        self.reply_to_message = None
        self.forward_origin = object() if forwarded else None
        self.voice = None
        self.video_note = None
        media = FakeMedia(**media_kwargs)
        if kind == "voice":
            self.voice = media
        else:
            media.mime_type = None  # у VideoNote mime_type немає
            self.video_note = media
        self.media = media
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return type("Sent", (), {"message_id": 99, "date": datetime.now(timezone.utc)})()


@pytest.fixture
def flow(monkeypatch):
    """Керований потік: БД і модель підмінені, кожен виклик записується."""
    monkeypatch.setattr(config, "VOICE_DAILY_LIMIT", LIMIT)
    monkeypatch.setattr(config, "VOICE_MAX_SECONDS", 300)

    state = {
        "used": 1,
        "analysis": VoiceAnalysis(kind="speech", transcript="привіт, я записав голосове"),
        "calls": [],
        "stored": [],
        "analyze_args": None,
    }

    async def fake_db_call(func, *args, **kwargs):
        state["calls"].append(func.__name__)
        if func.__name__ == "claim_voice_slot":
            return state["used"]
        if func.__name__ == "add_message":
            # (chat_id, message_id, user_id, username, full_name, text, reply_to, ts)
            state["stored"].append({"message_id": args[1], "user_id": args[2], "text": args[5]})
        return None

    async def fake_analyze(data, mime_type, media, chat_id=None):
        state["calls"].append("analyze_voice")
        state["analyze_args"] = (data, mime_type, media, chat_id)
        if isinstance(state["analysis"], Exception):
            raise state["analysis"]
        return state["analysis"]

    monkeypatch.setattr(reply_mod, "db_call", fake_db_call)
    monkeypatch.setattr(reply_mod, "analyze_voice", fake_analyze)
    return state


async def test_speech_replies_with_intro_and_transcript(flow):
    msg = FakeMessage()

    await reply_mod.handle_voice(msg)

    assert len(msg.sent) == 1
    text, kwargs = msg.sent[0]
    assert kwargs["parse_mode"] == "HTML"
    assert "<blockquote expandable>привіт, я записав голосове</blockquote>" in text
    assert flow["analyze_args"] == (b"OggS", "audio/ogg", "voice", CHAT)
    assert msg.chat.actions == ["typing"]

    voice_row, bot_row = flow["stored"]
    assert voice_row == {"message_id": 7, "user_id": 1, "text": "🎤 привіт, я записав голосове"}
    assert bot_row["message_id"] == 99
    assert bot_row["user_id"] == config.BOT_USER_ID
    assert "привіт, я записав" not in bot_row["text"], "розшифровка в історії має бути один раз"
    assert bot_row["text"] and "<" not in bot_row["text"]


async def test_snoring_says_wake_up_without_transcript(flow):
    flow["analysis"] = VoiceAnalysis(kind="snoring")
    msg = FakeMessage()

    await reply_mod.handle_voice(msg)

    text, _ = msg.sent[0]
    assert "blockquote" not in text
    assert text in phrases("snoring.j2", duration=12)
    assert flow["stored"][0]["text"] == "[🎤 голосове: хропіння]"


async def test_no_speech_gets_noise_reply(flow):
    flow["analysis"] = VoiceAnalysis(kind="other")
    msg = FakeMessage()

    await reply_mod.handle_voice(msg)

    assert msg.sent[0][0] in phrases("noise.j2", duration=12)
    assert flow["stored"][0]["text"] == "[🎤 голосове без слів]"


async def test_video_note_goes_to_gemini_as_mp4(flow):
    msg = FakeMessage(kind="video_note")

    await reply_mod.handle_voice(msg)

    assert flow["analyze_args"][1:3] == ("video/mp4", "video_note")
    assert flow["stored"][0]["text"].startswith("📹 ")
    assert msg.sent[0][0].count("📹") == 1


async def test_too_long_skips_download_model_and_limit(flow):
    msg = FakeMessage(duration=301)

    await reply_mod.handle_voice(msg)

    assert msg.media.downloads == 0
    assert "analyze_voice" not in flow["calls"]
    assert "claim_voice_slot" not in flow["calls"], "нерозшифроване не має зʼїдати ліміт"
    assert msg.sent[0][0] in phrases("too_long.j2", duration=301)
    assert flow["stored"][0]["text"] == "[🎤 голосове, 5 хвилин 1 секунда, не розшифровано]"


async def test_oversized_file_is_treated_as_too_long(flow):
    msg = FakeMessage(file_size=reply_mod.MAX_INLINE_BYTES + 1)

    await reply_mod.handle_voice(msg)

    assert "analyze_voice" not in flow["calls"]
    assert len(msg.sent) == 1


async def test_first_voice_over_limit_gets_one_warning(flow):
    flow["used"] = LIMIT + 1
    msg = FakeMessage()

    await reply_mod.handle_voice(msg)

    assert "analyze_voice" not in flow["calls"]
    assert msg.media.downloads == 0
    assert msg.sent[0][0] in phrases("limit.j2")
    assert flow["stored"][0]["text"] == "[🎤 голосове]", "голосове все одно видно в історії"


async def test_further_voices_over_limit_are_ignored_silently(flow):
    flow["used"] = LIMIT + 2
    msg = FakeMessage()

    await reply_mod.handle_voice(msg)

    assert msg.sent == []
    assert "analyze_voice" not in flow["calls"]
    assert flow["stored"] == [{"message_id": 7, "user_id": 1, "text": "[🎤 голосове]"}]


async def test_last_voice_within_limit_is_transcribed(flow):
    flow["used"] = LIMIT
    msg = FakeMessage()

    await reply_mod.handle_voice(msg)

    assert "analyze_voice" in flow["calls"]
    assert "blockquote" in msg.sent[0][0]


async def test_failure_returns_the_slot_and_still_answers(flow):
    flow["analysis"] = RuntimeError("Gemini впав")
    msg = FakeMessage()

    await reply_mod.handle_voice(msg)

    assert "release_voice_slot" in flow["calls"], "технічний збій ліміт не зʼїдає"
    assert msg.sent[0][0] in phrases("failed.j2")
    assert flow["stored"][0]["text"] == "[🎤 голосове]"


async def test_chat_action_failure_does_not_block_reply(flow):
    msg = FakeMessage(fail_action=True)

    await reply_mod.handle_voice(msg)

    assert "blockquote" in msg.sent[0][0]


async def test_send_failure_is_swallowed(flow):
    msg = FakeMessage()

    async def broken_reply(text, **kwargs):
        raise RuntimeError("Telegram лежить")

    msg.reply_text = broken_reply

    await reply_mod.handle_voice(msg)  # не має кинути — це фонова задача


async def test_forwarded_voice_mocks_the_forwarder(flow, first_phrase):
    msg = FakeMessage(forwarded=True)

    await reply_mod.handle_voice(msg)

    expected = render("speech.j2", **context(forwarded=True, duration=12))
    assert msg.sent[0][0].startswith(expected)
    assert expected != render("speech.j2", **context(duration=12))


async def test_creator_gets_respectful_reply(flow):
    msg = FakeMessage()

    await reply_mod.handle_voice(msg, is_creator=True)

    intro = msg.sent[0][0].split("\n\n")[0]
    assert intro in phrases("speech.j2", is_creator=True, duration=12)


async def test_message_without_voice_is_ignored(flow):
    msg = FakeMessage()
    msg.voice = None

    await reply_mod.handle_voice(msg)

    assert flow["calls"] == []
    assert msg.sent == []


# --- реєстрація хендлерів ---

def _update(**media):
    from telegram import Chat, Message, Update

    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=CHAT, type="supergroup"),
        **media,
    )
    return Update(update_id=1, message=message)


def test_voice_and_video_note_go_to_voice_handler_not_text_handler():
    from telegram import VideoNote, Voice

    from src.main import TEXT_FILTER, VOICE_FILTER

    for update in (
        _update(voice=Voice(file_id="f", file_unique_id="u", duration=3)),
        _update(video_note=VideoNote(file_id="f", file_unique_id="u", length=240, duration=3)),
    ):
        assert VOICE_FILTER.check_update(update)
        assert not TEXT_FILTER.check_update(update), "загальний хендлер ковтав голосові мовчки"

    text = _update(text="привіт")
    assert TEXT_FILTER.check_update(text)
    assert not VOICE_FILTER.check_update(text)


class FakeApplication:
    def __init__(self):
        self.tasks = []

    def create_task(self, coroutine, name=None):
        self.tasks.append(name)
        coroutine.close()


async def test_on_voice_schedules_only_in_voice_chats(monkeypatch):
    """Розшифровка вмикається по чатах: PanBot-чат без VOICE_CHAT_IDS голосові не чіпає."""
    import src.tools.handlers as handlers

    scheduled = []

    async def fake_db_call(func, *args, **kwargs):
        return None

    monkeypatch.setattr(handlers, "db_call", fake_db_call)
    monkeypatch.setattr(
        handlers, "schedule_voice",
        lambda context, msg, is_creator=False: scheduled.append(is_creator),
    )

    msg = FakeMessage(user_id=handlers.CREATOR_USER_ID)
    update = type("Upd", (), {"effective_message": msg, "effective_chat": msg.chat})()

    monkeypatch.setattr(config, "PANBOT_CHAT_IDS", {CHAT})

    # Порожній список — безпечний дефолт першого деплою: функція вимкнена.
    monkeypatch.setattr(config, "VOICE_CHAT_IDS", set())
    await handlers.on_voice(update, None)
    assert scheduled == []

    monkeypatch.setattr(config, "VOICE_CHAT_IDS", {CHAT + 1})
    await handlers.on_voice(update, None)
    assert scheduled == [], "чат не зі списку не має запускати розшифровку"

    monkeypatch.setattr(config, "VOICE_CHAT_IDS", {CHAT, CHAT + 1})
    await handlers.on_voice(update, None)
    assert scheduled == [True]


def test_schedule_voice_runs_in_background():
    app = FakeApplication()
    ctx = type("Ctx", (), {"application": app})()

    reply_mod.schedule_voice(ctx, FakeMessage())

    assert app.tasks == [f"voice:{CHAT}"]
