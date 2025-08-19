import os
import logging
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TZ = os.getenv("TZ", "Europe/Kyiv")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-pro")

_allow_env = os.getenv("ALLOWED_CHAT_IDS") or os.getenv("ALLOWED_CHAT_ID")
ALLOWED_CHAT_IDS = None
if _allow_env:
    ALLOWED_CHAT_IDS = {int(x.strip()) for x in _allow_env.split(",") if x.strip()}

KYIV = ZoneInfo(TZ)
DB_PATH = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("daily-summary-bot")