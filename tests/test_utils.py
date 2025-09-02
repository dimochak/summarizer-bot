# tests/test_utils.py
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest

import src.utils as utils

# Ensure project root is on sys.path so `import src...` works
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Ensure required env vars before importing src.config/src.utils
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("TZ", "Europe/Kyiv")


def test_utc_ts():
    dt_utc = datetime(2024, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC"))
    assert utils.utc_ts(dt_utc) == 1704067200


def test_local_midnight_bounds_basic():
    kyiv = ZoneInfo("Europe/Kyiv")
    day_local = datetime(2024, 1, 15, 13, 45, tzinfo=ZoneInfo("UTC")).astimezone(kyiv)
    start_local, end_local = utils.local_midnight_bounds(day_local)
    assert start_local.tzinfo == kyiv
    assert end_local.tzinfo == kyiv
    assert start_local == datetime(2024, 1, 15, 0, 0, tzinfo=kyiv)
    assert end_local == datetime(2024, 1, 16, 0, 0, tzinfo=kyiv)


def test_local_midnight_bounds_cross_tz_day_boundary():
    kyiv = ZoneInfo("Europe/Kyiv")
    # 2024-01-14 22:30 UTC -> 2024-01-15 00:30 Europe/Kyiv
    utc_dt = datetime(2024, 1, 14, 22, 30, tzinfo=ZoneInfo("UTC"))
    day_local = utc_dt.astimezone(kyiv)
    start_local, end_local = utils.local_midnight_bounds(day_local)
    assert start_local == datetime(2024, 1, 15, 0, 0, tzinfo=kyiv)
    assert end_local == datetime(2024, 1, 16, 0, 0, tzinfo=kyiv)


def test_message_link_with_username():
    chat = SimpleNamespace(id=-1001234567890, username="mychannel")
    url = utils.message_link(chat, 42)
    assert url == "https://t.me/mychannel/42"


@pytest.mark.parametrize(
    "chat_id,expected_prefix",
    [
        (-100987654321, "https://t.me/c/987654321/"),  # supergroup id: strip -100
        (-123456, "https://t.me/c/123456/"),  # negative id: strip leading -
        (123456, "https://t.me/c/123456/"),  # positive id
    ],
)
def test_message_link_id_variants(chat_id, expected_prefix):
    chat = SimpleNamespace(id=chat_id, username=None)
    url = utils.message_link(chat, 77)
    assert url == f"{expected_prefix}77"


def test_user_link_with_username_and_escape_label():
    # Full name contains HTML special chars; should be escaped in label
    html = utils.user_link(user_id=1, username="user_name", full_name="<Admin & Co>")
    assert html == '<a href="https://t.me/user_name">&lt;Admin &amp; Co&gt;</a>'


def test_user_link_without_username_uses_tg_user_scheme_and_label_fallback():
    # If full_name is empty but username missing, fallback to "Користувач"
    html = utils.user_link(user_id=12345, username=None, full_name="")
    assert html == '<a href="tg://user?id=12345">Користувач</a>'


def test_user_link_without_username_with_full_name():
    html = utils.user_link(user_id=42, username=None, full_name="John Doe")
    assert html == '<a href="tg://user?id=42">John Doe</a>'


def test_clean_text_none_and_whitespace():
    assert utils.clean_text(None) == ""
    assert utils.clean_text("") == ""
    assert utils.clean_text("   spaced   ") == "spaced"
