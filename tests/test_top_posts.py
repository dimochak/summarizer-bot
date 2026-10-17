"""Топ постів за реакціями в щоденному дайджесті."""
from datetime import datetime, timezone

import pytest
from telegram import (
    Chat,
    MessageReactionUpdated,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
    ReactionTypePaid,
    Update,
    User,
)
from telegram.ext import MessageHandler, filters

import src.main as main_mod
import src.summarizer.summarizer as summarizer_mod
import src.tools.config as config
import src.top_posts.digest as digest
import src.top_posts.tracking as tracking

CHAT_ID = -1001856168782
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def reaction_update(*emojis, user=User(1, "Петро", False), actor_chat=None, chat_id=CHAT_ID):
    return Update(
        update_id=1,
        message_reaction=MessageReactionUpdated(
            chat=Chat(chat_id, Chat.SUPERGROUP),
            message_id=42,
            date=NOW,
            old_reaction=(),
            new_reaction=tuple(emojis),
            user=user,
            actor_chat=actor_chat,
        ),
    )


# --- реакції не мають запускати відповідь бота ---

def test_reaction_update_does_not_reach_message_handlers():
    """Колись бот відповідав повторно саме на реакції. Апдейт реакції не має
    збігатися ні з on_message, ні з реєстром постів."""
    update = reaction_update(ReactionTypeEmoji("👍"))
    for flt in (main_mod.TEXT_FILTER, main_mod.VOICE_FILTER,
                ~filters.StatusUpdate.ALL & ~filters.COMMAND):
        assert not MessageHandler(flt, lambda *a: None).check_update(update)


# --- тип і прев'ю поста ---

class Msg:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, name):
        return None


@pytest.mark.parametrize(
    "fields, kind",
    [
        ({"text": "привіт"}, "text"),
        ({"photo": [object()], "caption": "мем"}, "photo"),
        ({"video": object()}, "video"),
        ({"animation": object(), "document": object()}, "animation"),
        ({"video_note": object()}, "video_note"),
        ({"sticker": Msg(emoji="😂")}, "sticker"),
        ({}, None),
    ],
)
def test_post_kind(fields, kind):
    assert tracking.post_kind(Msg(**fields)) == kind


def test_preview_uses_caption_and_sticker_emoji():
    assert tracking.post_preview(Msg(photo=[1], caption="підпис"), "photo") == "підпис"
    assert tracking.post_preview(Msg(sticker=Msg(emoji="😂")), "sticker") == "😂"
    assert tracking.post_preview(Msg(video=1), "video") is None
    assert len(tracking.post_preview(Msg(text="а" * 500), "text")) == tracking.PREVIEW_LIMIT


def test_reaction_keys():
    assert tracking.reaction_key(ReactionTypeEmoji("❤")) == "❤"
    assert tracking.reaction_key(ReactionTypeCustomEmoji("123")) == "custom:123"
    assert tracking.reaction_key(ReactionTypePaid()) == "paid"


# --- запис реакцій ---

@pytest.fixture
def saved(monkeypatch):
    calls = []

    async def fake_db_call(func, *args):
        calls.append((func.__name__, args))

    monkeypatch.setattr(tracking, "db_call", fake_db_call)
    monkeypatch.setattr(config, "KNOWN_CHAT_IDS", {CHAT_ID})
    return calls


async def test_user_reaction_saved(saved):
    await tracking.on_reaction(reaction_update(ReactionTypeEmoji("👍"), ReactionTypeEmoji("🔥")), None)
    assert saved == [("set_message_reactions", (CHAT_ID, 42, 1, ["👍", "🔥"], int(NOW.timestamp())))]


async def test_removed_reaction_saves_empty_set(saved):
    await tracking.on_reaction(reaction_update(), None)
    assert saved[0][1][3] == []


async def test_anonymous_admin_reacts_as_chat(saved):
    update = reaction_update(ReactionTypeEmoji("👍"), user=None,
                             actor_chat=Chat(CHAT_ID, Chat.SUPERGROUP))
    await tracking.on_reaction(update, None)
    assert saved[0][1][2] == CHAT_ID


async def test_unknown_chat_ignored(saved):
    await tracking.on_reaction(reaction_update(ReactionTypeEmoji("👍"), chat_id=-1), None)
    assert saved == []


# --- форматування ---

CHAT = Chat(CHAT_ID, Chat.SUPERGROUP)


def row(message_id, reactors, kind="text", preview="текст", name="Петро", reactions=("👍",)):
    return {"message_id": message_id, "user_id": 1, "username": None, "full_name": name,
            "kind": kind, "preview": preview, "reactors": reactors, "reactions": list(reactions)}


def test_block_has_links_counts_and_labels():
    block = digest.format_top_posts(CHAT, [
        row(10, 7, reactions=["❤", "❤", "😂", "👍", "🔥"]),
        row(11, 3, kind="photo", preview=None),
        row(12, 2, kind="video", preview="дивіться"),
    ])
    lines = block.split("\n")
    assert lines[0] == "<b>🔥 Топ-3 за реакціями</b>"
    assert lines[1] == '1. <a href="https://t.me/c/1856168782/10">текст</a> — Петро · 7 ❤😂👍'
    assert '<a href="https://t.me/c/1856168782/11">📷 фото</a>' in lines[2]
    assert "🎬 відео: дивіться" in lines[3]


def test_block_escapes_user_text():
    block = digest.format_top_posts(CHAT, [row(1, 1, preview="<b>ой</b>", name="<script>")])
    assert "<b>ой</b>" not in block and "&lt;b&gt;ой&lt;/b&gt;" in block
    assert "&lt;script&gt;" in block


def test_long_preview_truncated_and_custom_emoji_shown():
    block = digest.format_top_posts(CHAT, [row(1, 1, preview="слово " * 30,
                                                reactions=["custom:9", "paid"])])
    assert "…" in block and "✨⭐" in block


def test_no_reactions_no_block():
    assert digest.format_top_posts(CHAT, []) == ""


# --- вбудовування в дайджест ---

async def test_block_appended_to_summary(monkeypatch):
    async def block(chat, start, end):
        return "<b>🔥 Топ-1 за реакціями</b>\n1. …"
    monkeypatch.setattr(summarizer_mod, "top_posts_block", block)

    result = await summarizer_mod._with_top_posts(CHAT, "<b>#Підсумки_дня</b>", 0, 1)
    assert result == "<b>#Підсумки_дня</b>\n\n<b>🔥 Топ-1 за реакціями</b>\n1. …"


async def test_block_dropped_when_digest_too_long(monkeypatch):
    async def block(chat, start, end):
        return "x" * 500
    monkeypatch.setattr(summarizer_mod, "top_posts_block", block)

    summary = "s" * 3800
    assert await summarizer_mod._with_top_posts(CHAT, summary, 0, 1) == summary


async def test_block_failure_keeps_summary(monkeypatch):
    async def boom(chat, start, end):
        raise RuntimeError("БД лягла")
    monkeypatch.setattr(summarizer_mod, "top_posts_block", boom)

    assert await summarizer_mod._with_top_posts(CHAT, "підсумок", 0, 1) == "підсумок"


# --- перевірка прав на старті ---

async def test_warns_when_bot_is_not_admin(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_CHAT_IDS", {1, 2})
    warnings = []
    monkeypatch.setattr(config, "log", type("L", (), {"warning": staticmethod(warnings.append)}))

    class Bot:
        id = 99

        async def get_chat_member(self, chat_id, user_id):
            return type("M", (), {"status": "administrator" if chat_id == 1 else "member"})()

    await tracking.warn_if_not_admin(Bot())
    assert len(warnings) == 1 and "чаті 2" in warnings[0]
