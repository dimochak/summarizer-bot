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

    # Only the allowed chat is summarized; the job passes the day window through.
    mock_send_daily_summary_to_chat.assert_awaited_once()
    args, _ = mock_send_daily_summary_to_chat.await_args
    assert args[0] is app
    assert args[1] == 456
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

    job_queue.run_daily.assert_called_once()
    call = job_queue.run_daily.call_args
    assert call.kwargs["name"] == "daily_summary_all"
    assert isinstance(call.kwargs["time"], dtime)
    fake_config.log.info.assert_called_with("Daily job scheduled for 23:59, Europe/Kyiv")
