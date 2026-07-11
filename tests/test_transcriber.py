from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.tools.handlers as handlers

CHAT_ID = -1003935118970


def _voice_update(transcript_msg_id=10, allowed=True):
    placeholder = SimpleNamespace(edit_text=AsyncMock())
    msg = SimpleNamespace(
        voice=SimpleNamespace(file_id="vf1", mime_type="audio/ogg"),
        video_note=None,
        from_user=SimpleNamespace(id=42, username="oleh", full_name="Олег"),
        message_id=transcript_msg_id,
        date=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        reply_to_message=None,
        reply_text=AsyncMock(return_value=placeholder),
    )
    chat = SimpleNamespace(id=CHAT_ID if allowed else 999)
    update = SimpleNamespace(effective_message=msg, effective_chat=chat)
    return update, msg, placeholder


@pytest.mark.asyncio
async def test_transcript_is_stored_and_replied():
    update, msg, placeholder = _voice_update()
    with patch.object(handlers.config, "ALLOWED_CHAT_IDS", {CHAT_ID}), \
         patch.object(handlers, "ensure_chat_record"), \
         patch.object(handlers, "transcribe_by_file_id", new=AsyncMock(return_value="привіт світ")), \
         patch.object(handlers, "add_message") as mock_add:
        await handlers.on_voice_video(update, MagicMock())

    # Stored under the original speaker/message so it feeds the daily summary.
    mock_add.assert_called_once()
    args = mock_add.call_args.args
    assert args[0] == CHAT_ID          # chat_id
    assert args[1] == msg.message_id   # message_id
    assert args[2] == 42               # user_id
    assert args[5] == "привіт світ"    # text
    # Replied with the transcript.
    reply_text = placeholder.edit_text.call_args.args[0]
    assert "привіт світ" in reply_text


@pytest.mark.asyncio
async def test_empty_transcript_not_stored():
    update, msg, placeholder = _voice_update()
    with patch.object(handlers.config, "ALLOWED_CHAT_IDS", {CHAT_ID}), \
         patch.object(handlers, "ensure_chat_record"), \
         patch.object(handlers, "transcribe_by_file_id", new=AsyncMock(return_value="")), \
         patch.object(handlers, "add_message") as mock_add:
        await handlers.on_voice_video(update, MagicMock())

    mock_add.assert_not_called()
    placeholder.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_disallowed_chat_is_ignored():
    update, msg, placeholder = _voice_update(allowed=False)
    with patch.object(handlers.config, "ALLOWED_CHAT_IDS", {CHAT_ID}), \
         patch.object(handlers, "transcribe_by_file_id", new=AsyncMock()) as mock_tx:
        await handlers.on_voice_video(update, MagicMock())

    msg.reply_text.assert_not_called()
    mock_tx.assert_not_called()
