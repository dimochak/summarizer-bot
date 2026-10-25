"""Реакції-картинки на згадки тем.

Головне тут — порядок перевірок: дешеві (регулярка, кулдаун, наявність
картинок) мають відсікати повідомлення ДО виклику моделі, а кулдаун —
витрачатись лише на підтверджену тему.
"""
import dataclasses
from datetime import datetime, timezone

import pytest

import src.reactions.react as react_mod
import src.reactions.rules as rules_mod
import src.tools.config as config

CHAT = -100500
KLITSCHKO = next(r for r in rules_mod.RULES if r.name == "klitschko")
TRUMP = next(r for r in rules_mod.RULES if r.name == "trump")


# --- регулярка ---

@pytest.mark.parametrize(
    "text",
    [
        "Кличко знову щось сказав",
        "мер Кличко відкрив міст",
        "новина про Кличка",
        "дякуємо Кличку за сьогоднішнє",
        "з Кличком таке буває",
        "Klitschko said something again",
    ],
)
def test_pattern_catches_surname_forms(text):
    assert react_mod.matching_rules(text) == [KLITSCHKO]


@pytest.mark.parametrize("text", ["привіт усім", "заклики до дії", "", None])
def test_pattern_ignores_unrelated(text):
    assert react_mod.matching_rules(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Трамп знову підвищив мита",
        "заява Трампа",
        "Трампу не сподобалось",
        "зустріч із Трампом",
        "Trump said something again",
    ],
)
def test_trump_pattern_catches_surname_forms(text):
    assert react_mod.matching_rules(text) == [TRUMP]


@pytest.mark.parametrize("text", ["стрибнув з трампліна", "новий трамплін", "he plays the trumpet"])
def test_trump_pattern_ignores_lookalikes(text):
    """Ці збіги відсікає сама регулярка — модель на них не кличеться."""
    assert react_mod.matching_rules(text) == []


def test_both_subjects_match_but_only_one_reaction_is_possible():
    assert react_mod.matching_rules("Кличко і Трамп в одному кадрі") == [KLITSCHKO, TRUMP]


def test_nickname_also_matches_regex_and_is_left_to_llm():
    """«Кличка» — ще й звичайне слово. Регулярка його ловить свідомо:
    відрізнити прізвище від прізвиська — робота LLM-підтвердження."""
    assert react_mod.matching_rules("яка кличка в твого собаки?") == [KLITSCHKO]


# --- вибір картинки ---

def test_pick_image_from_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(rules_mod, "ASSETS_DIR", tmp_path)
    folder = tmp_path / "klitschko"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(b"x")
    (folder / "b.gif").write_bytes(b"x")
    (folder / "README.md").write_text("не картинка")

    assert react_mod.pick_image(KLITSCHKO).name in {"a.jpg", "b.gif"}


def test_pick_image_empty_or_missing_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(rules_mod, "ASSETS_DIR", tmp_path)
    assert react_mod.pick_image(KLITSCHKO) is None

    folder = tmp_path / "klitschko"
    folder.mkdir()
    (folder / "README.md").write_text("лише опис")
    assert react_mod.pick_image(KLITSCHKO) is None


# --- повний потік ---

class FakeMessage:
    def __init__(self):
        self.message_id = 7
        self.chat = type("Chat", (), {"id": CHAT})()
        self.from_user = type("U", (), {"id": 1, "full_name": "Петро"})()
        self.sent = []

    async def _reply(self, kind, kwargs):
        self.sent.append((kind, kwargs))
        return type("Sent", (), {"message_id": 99, "date": datetime.now(timezone.utc)})()

    async def reply_photo(self, photo, **kwargs):
        return await self._reply("photo", kwargs)

    async def reply_animation(self, animation, **kwargs):
        return await self._reply("animation", kwargs)


@pytest.fixture
def flow(monkeypatch, tmp_path):
    """Керований потік: кожен крок можна перемкнути й перевірити, чи його кликали."""
    monkeypatch.setattr(config, "PANBOT_CHAT_IDS", {CHAT})

    image = tmp_path / "klitschko.jpg"
    image.write_bytes(b"jpeg")

    state = {
        "on_cooldown": False,
        "image": image,
        "confirmed": True,
        "claimed": True,
        "caption": "Мер знову перевершив сам себе.",
        "calls": [],
    }

    async def fake_db_call(func, *args, **kwargs):
        state["calls"].append(func.__name__)
        if func.__name__ == "is_reaction_on_cooldown":
            return state["on_cooldown"]
        if func.__name__ == "claim_reaction":
            return state["claimed"]
        return None

    async def fake_confirm(rule, text, chat_id):
        state["calls"].append("confirm_mention")
        return state["confirmed"]

    async def fake_caption(message, rule, text):
        state["calls"].append("build_caption")
        if isinstance(state["caption"], Exception):
            raise state["caption"]
        return state["caption"]

    monkeypatch.setattr(react_mod, "db_call", fake_db_call)
    monkeypatch.setattr(react_mod, "confirm_mention", fake_confirm)
    monkeypatch.setattr(react_mod, "pick_image", lambda rule: state["image"])
    monkeypatch.setattr(react_mod, "build_caption", fake_caption)
    return state


@pytest.fixture
def caption_rule(monkeypatch):
    """Правило з увімкненим підписом — Кличко його свідомо не має."""
    rule = dataclasses.replace(KLITSCHKO, generate_caption=True)
    monkeypatch.setattr(react_mod, "matching_rules", lambda text, message=None: [rule])
    return rule


async def test_klitschko_sends_picture_without_caption(flow):
    """Для Кличка картинка говорить сама за себе — модель підпис не пише."""
    msg = FakeMessage()

    assert await react_mod.maybe_react(msg, "Кличко знову щось сказав", CHAT) is True

    assert len(msg.sent) == 1
    kind, kwargs = msg.sent[0]
    assert kind == "photo"
    assert "caption" not in kwargs
    assert "build_caption" not in flow["calls"], "підпис вимкнений — модель не кличемо"
    assert "add_message" not in flow["calls"], "без підпису в історію писати нічого"


async def test_rule_with_caption_sends_caption(flow, caption_rule):
    msg = FakeMessage()

    assert await react_mod.maybe_react(msg, "Кличко знову щось сказав", CHAT) is True

    kind, kwargs = msg.sent[0]
    assert kind == "photo"
    assert "перевершив" in kwargs["caption"]
    assert "add_message" in flow["calls"], "підпис бота має лягти в історію чату"


async def test_disabled_chat_does_nothing(flow, monkeypatch):
    monkeypatch.setattr(config, "PANBOT_CHAT_IDS", set())
    assert await react_mod.maybe_react(FakeMessage(), "Кличко", CHAT) is False
    assert flow["calls"] == []


async def test_cooldown_skips_the_model(flow):
    flow["on_cooldown"] = True
    msg = FakeMessage()

    assert await react_mod.maybe_react(msg, "Кличко", CHAT) is False
    assert "confirm_mention" not in flow["calls"], "на кулдауні модель кликати не треба"
    assert msg.sent == []


async def test_no_images_skips_the_model(flow):
    flow["image"] = None
    assert await react_mod.maybe_react(FakeMessage(), "Кличко", CHAT) is False
    assert "confirm_mention" not in flow["calls"]


async def test_false_match_does_not_spend_cooldown(flow):
    """«Кличка собаки» не має зʼїдати кулдаун — інакше бот промовчить на справжню новину."""
    flow["confirmed"] = False
    msg = FakeMessage()

    assert await react_mod.maybe_react(msg, "яка кличка в собаки?", CHAT) is False
    assert "claim_reaction" not in flow["calls"]
    assert msg.sent == []


async def test_lost_claim_race_sends_nothing(flow):
    flow["claimed"] = False
    msg = FakeMessage()

    assert await react_mod.maybe_react(msg, "Кличко", CHAT) is False
    assert msg.sent == []
    assert "build_caption" not in flow["calls"]


async def test_caption_failure_still_sends_picture(flow, caption_rule):
    flow["caption"] = RuntimeError("LLM недоступний")
    msg = FakeMessage()

    assert await react_mod.maybe_react(msg, "Кличко", CHAT) is True
    kind, kwargs = msg.sent[0]
    assert kind == "photo"
    assert "caption" not in kwargs
    assert "add_message" not in flow["calls"], "без підпису в історію писати нічого"


async def test_animation_sent_as_animation(flow, tmp_path):
    gif = tmp_path / "klitschko.gif"
    gif.write_bytes(b"gif")
    flow["image"] = gif
    msg = FakeMessage()

    await react_mod.maybe_react(msg, "Кличко", CHAT)
    assert msg.sent[0][0] == "animation"


async def test_unexpected_error_does_not_propagate(flow, monkeypatch):
    """Реакція працює у фоні: виняток звідти нікуди не дійде, тож його треба зловити."""
    async def boom(*args, **kwargs):
        raise RuntimeError("БД лягла")

    monkeypatch.setattr(react_mod, "db_call", boom)
    assert await react_mod.maybe_react(FakeMessage(), "Кличко", CHAT) is False


# --- підпис ---

async def test_caption_is_truncated_to_telegram_limit(monkeypatch):
    async def long_reply(**kwargs):
        return "слово " * 500

    async def fake_db_call(func, *args, **kwargs):
        return "" if func.__name__ == "get_traits_block" else None

    monkeypatch.setattr(react_mod._engine, "generate_response", long_reply)
    monkeypatch.setattr(react_mod, "db_call", fake_db_call)

    caption = await react_mod.build_caption(FakeMessage(), KLITSCHKO, "Кличко")
    assert len(caption) <= react_mod.CAPTION_LIMIT
    assert caption.endswith("…")


# --- запуск у фоні ---

class FakeApplication:
    def __init__(self):
        self.tasks = []

    def create_task(self, coroutine, name=None):
        self.tasks.append(name)
        coroutine.close()  # не виконуємо — лише перевіряємо, що задачу поставили


@pytest.mark.parametrize(
    "text, chats, expected",
    [
        ("Кличко знову", {CHAT}, 1),
        ("просто розмова", {CHAT}, 0),   # регулярка не збіглась — задачу не створюємо
        ("Кличко знову", set(), 0),      # чат не увімкнений
        (None, {CHAT}, 0),               # фото без підпису
    ],
)
def test_schedule_reaction_only_for_candidates(monkeypatch, text, chats, expected):
    monkeypatch.setattr(config, "PANBOT_CHAT_IDS", chats)
    app = FakeApplication()
    context = type("Ctx", (), {"application": app})()

    react_mod.schedule_reaction(context, FakeMessage(), text, CHAT)

    assert len(app.tasks) == expected


# --- стартовий лог ---

def test_startup_log_reports_missing_empty_and_filled(tmp_path, monkeypatch):
    """Картинки живуть поза репозиторієм, тож порожня тека чи хибний шлях —
    найімовірніша поломка. Лог на старті має це показати."""
    messages = []
    fake_log = type("Log", (), {
        "info": staticmethod(lambda m: messages.append(("info", m))),
        "warning": staticmethod(lambda m: messages.append(("warning", m))),
    })
    monkeypatch.setattr(config, "log", fake_log)
    monkeypatch.setattr(rules_mod, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(react_mod, "RULES", [KLITSCHKO])

    react_mod.log_reaction_assets()
    assert messages[-1][0] == "warning" and "немає" in messages[-1][1]

    folder = tmp_path / "klitschko"
    folder.mkdir()
    (folder / "README.md").write_text("опис")
    react_mod.log_reaction_assets()
    assert messages[-1][0] == "warning" and "0 картинок" in messages[-1][1]

    (folder / "a.jpg").write_bytes(b"x")
    (folder / "b.mp4").write_bytes(b"x")
    react_mod.log_reaction_assets()
    assert messages[-1] == ("info", f"Reaction 'klitschko': 2 картинок у {folder}")


# --- тег людини ---

from telegram import Chat as TgChat, Message as TgMessage, MessageEntity, User as TgUser

TAGGED_ID = 777000111
FRIEND = rules_mod.ReactionRule(
    name="friend", cooldown_seconds=3600,
    mention_user_ids=frozenset({TAGGED_ID}), mention_usernames=frozenset({"fake_friend"}),
)


def tg_message(text, entities=(), author=TgUser(1, "Петро", False, username="petro_fake"),
               caption=False):
    kwargs = {"caption": text, "caption_entities": tuple(entities)} if caption else \
             {"text": text, "entities": tuple(entities)}
    return TgMessage(message_id=7, date=datetime.now(timezone.utc),
                     chat=TgChat(CHAT, TgChat.SUPERGROUP), from_user=author, **kwargs)


@pytest.fixture
def friend_rule(monkeypatch):
    monkeypatch.setattr(react_mod, "MENTION_RULES", [FRIEND])
    return FRIEND


def test_username_tag_matches(friend_rule):
    text = "@Fake_Friend глянь"
    msg = tg_message(text, [MessageEntity(MessageEntity.MENTION, 0, 12)])
    assert react_mod.matching_rules(text, msg) == [FRIEND]


def test_tag_without_username_matches_by_id(friend_rule):
    """Людина без юзернейма: тег через вибір зі списку — text_mention з ID."""
    text = "Оля, глянь"
    entity = MessageEntity(MessageEntity.TEXT_MENTION, 0, 3, user=TgUser(TAGGED_ID, "Оля", False))
    assert react_mod.matching_rules(text, tg_message(text, [entity])) == [FRIEND]


def test_tag_in_photo_caption_matches(friend_rule):
    text = "@fake_friend це ти"
    msg = tg_message(text, [MessageEntity(MessageEntity.MENTION, 0, 12)], caption=True)
    assert react_mod.matching_rules(text, msg) == [FRIEND]


@pytest.mark.parametrize("text", [
    "fake_friend без собачки",        # просто текст, тегу немає
    "пишу тобі на email fake_friend@example.com",
])
def test_plain_text_is_not_a_tag(friend_rule, text):
    assert react_mod.matching_rules(text, tg_message(text)) == []


def test_other_person_tag_ignored(friend_rule):
    text = "@someone_else привіт"
    msg = tg_message(text, [MessageEntity(MessageEntity.MENTION, 0, 13)])
    assert react_mod.matching_rules(text, msg) == []


def test_self_tag_ignored(friend_rule):
    me = TgUser(TAGGED_ID, "Оля", False, username="fake_friend")
    text = "@fake_friend це я"
    msg = tg_message(text, [MessageEntity(MessageEntity.MENTION, 0, 12)], author=me)
    assert react_mod.matching_rules(text, msg) == []


async def test_tag_reaction_skips_the_model(flow, friend_rule, monkeypatch):
    """Telegram сам каже, кого тегнули, — модель для перевірки не потрібна."""
    text = "@fake_friend дивись"
    msg = tg_message(text, [MessageEntity(MessageEntity.MENTION, 0, 12)])
    sent = []

    async def capture(message, image, caption):
        sent.append(image)
        return type("Sent", (), {"message_id": 1, "date": datetime.now(timezone.utc)})()

    monkeypatch.setattr(react_mod, "_send", capture)
    assert await react_mod.maybe_react(msg, text, CHAT) is True
    assert "confirm_mention" not in flow["calls"]
    assert "claim_reaction" in flow["calls"], "кулдаун діє і для тегів"
    assert len(sent) == 1


# --- розбір MENTION_REACTIONS ---

def test_parse_mention_reactions_groups_keys_by_folder():
    rules = rules_mod.parse_mention_reactions(
        f"@Fake_Friend=friend, {TAGGED_ID}=friend, 42=other"
    )
    by_name = {r.name: r for r in rules}
    assert by_name["friend"].mention_usernames == {"fake_friend"}
    assert by_name["friend"].mention_user_ids == {TAGGED_ID}
    assert by_name["other"].mention_user_ids == {42}
    assert all(r.cooldown_seconds == 3600 and not r.needs_confirmation for r in rules)


@pytest.mark.parametrize("raw", ["", None, "  ,  "])
def test_parse_empty(raw):
    assert rules_mod.parse_mention_reactions(raw) == []


def test_parse_skips_bad_entries_without_leaking_them(monkeypatch):
    warnings = []
    monkeypatch.setattr(config, "log", type("L", (), {"warning": staticmethod(warnings.append)}))
    rules = rules_mod.parse_mention_reactions("@fake_friend, 123=../etc, 42=ok")
    assert [r.name for r in rules] == ["ok"]
    assert len(warnings) == 2
    assert not any("fake_friend" in w or "123" in w for w in warnings), "у лог — лише номер запису"
