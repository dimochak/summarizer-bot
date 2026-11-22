import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo
from datetime import time as dtime

import src.tools.scheduler as scheduler

@pytest.mark.asyncio
@patch("src.tools.scheduler.send_daily_summary_to_chat", new_callable=AsyncMock)
@patch("src.tools.scheduler.config")
@patch("src.tools.scheduler.get_enabled_chat_ids")
async def test_send_all_summaries_job_sends_only_configured_chats(
    mock_get_enabled_chat_ids,
    mock_config,
    mock_send_daily_summary_to_chat
):
    # Setup: 2 enabled, only 1 allowed
    mock_get_enabled_chat_ids.return_value = [123, 456]
    mock_config.ALLOWED_CHAT_IDS = [456]
    mock_config.KYIV = ZoneInfo("Europe/Kyiv")
    mock_config.log = MagicMock()

    app = MagicMock()
    context = MagicMock()
    context.application = app

    await scheduler.send_all_summaries_job(context)

    # Only allowed chat is sent
    mock_send_daily_summary_to_chat.assert_awaited_once()
    call_args = mock_send_daily_summary_to_chat.await_args.args
    assert call_args[0] is app
    assert call_args[1] == 456
    mock_config.log.info.assert_called_with("Daily summaries sent to 1 chats")

@pytest.mark.asyncio
@patch("src.tools.scheduler.send_daily_summary_to_chat", new_callable=AsyncMock)
@patch("src.tools.scheduler.config")
@patch("src.tools.scheduler.get_enabled_chat_ids")
async def test_send_all_summaries_no_configured_chats(
    mock_get_enabled_chat_ids,
    mock_config,
    mock_send_daily_summary_to_chat
):
    mock_get_enabled_chat_ids.return_value = [222]
    mock_config.ALLOWED_CHAT_IDS = []
    mock_config.KYIV = ZoneInfo("Europe/Kyiv")
    mock_config.log = MagicMock()

    app = MagicMock()
    context = MagicMock()
    context.application = app

    await scheduler.send_all_summaries_job(context)

    mock_send_daily_summary_to_chat.assert_not_awaited()
    mock_config.log.info.assert_called_with("No enabled and configured chats to summarize.")


def test_schedule_daily_adds_job(monkeypatch):
    app = MagicMock()
    job_queue = MagicMock()
    app.job_queue = job_queue

    fake_config = MagicMock()
    monkeypatch.setattr(scheduler, "config", fake_config)
    fake_config.KYIV = ZoneInfo("Europe/Kyiv")
    fake_config.TZ = "Europe/Kyiv"
    fake_config.log = MagicMock()

    scheduler.schedule_daily(app)

    # Три джоби: щоденний підсумок, прибирання БД і оновлення профілів
    assert job_queue.run_daily.call_count == 3

    jobs = {c.kwargs["name"]: c.kwargs for c in job_queue.run_daily.call_args_list}
    assert set(jobs) == {"daily_summary_all", "db_cleanup", "traits_refresh"}

    summary = jobs["daily_summary_all"]
    assert isinstance(summary["time"], dtime)
    assert (summary["time"].hour, summary["time"].minute) == (23, 59)

    cleanup = jobs["db_cleanup"]
    assert isinstance(cleanup["time"], dtime)
    assert (cleanup["time"].hour, cleanup["time"].minute) == (4, 0)

    traits = jobs["traits_refresh"]
    assert isinstance(traits["time"], dtime)
    # Після прибирання БД, щоб працювати по підчищеній таблиці messages
    assert traits["time"].hour > cleanup["time"].hour


# --- щоденний пост: лише топ за реакціями ---

from datetime import datetime


class _Bot:
    def __init__(self):
        self.sent = []

    async def get_chat(self, chat_id):
        return type("Chat", (), {"id": chat_id})()

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


KYIV = ZoneInfo("Europe/Kyiv")
DAY_START = datetime(2026, 11, 22, 0, 0, tzinfo=KYIV)
DAY_END = datetime(2026, 11, 23, 0, 0, tzinfo=KYIV)


@pytest.mark.asyncio
async def test_daily_post_is_only_top_by_reactions(monkeypatch):
    windows = []

    async def block(chat, start_ts, end_ts):
        windows.append((start_ts, end_ts))
        return "<b>🔥 Топ-1 за реакціями</b>\n1. посилання — 👍 3 — Петро"

    monkeypatch.setattr(scheduler, "top_posts_block", block)
    bot = _Bot()
    await scheduler.send_daily_summary_to_chat(type("App", (), {"bot": bot})(), -100, DAY_START, DAY_END)

    text = bot.sent[0]["text"]
    assert text.startswith("<b>#Підсумки_дня — 22.11.2026</b>")
    assert "🔥 Топ-1 за реакціями" in text
    assert "/summary_now" in text, "підказка, де взяти повний підсумок"
    assert windows == [(int(DAY_START.timestamp()), int(DAY_END.timestamp()))]


@pytest.mark.asyncio
async def test_daily_post_skipped_without_reactions(monkeypatch):
    async def empty(chat, start_ts, end_ts):
        return ""

    monkeypatch.setattr(scheduler, "top_posts_block", empty)
    bot = _Bot()
    await scheduler.send_daily_summary_to_chat(type("App", (), {"bot": bot})(), -100, DAY_START, DAY_END)
    assert bot.sent == []


def test_daily_job_no_longer_generates_llm_summary():
    """Текстовий підсумок — лише за /summary_now; щоденний джоб модель не кличе."""
    assert not hasattr(scheduler, "summarize_day")
