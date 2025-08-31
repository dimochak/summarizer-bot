import os
import logging
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

TZ = os.getenv("TZ", "Europe/Kyiv")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")

# Configuration for Gemini-enabled chat IDs
_gemini_env = os.getenv("GEMINI_CHAT_IDS")
GEMINI_CHAT_IDS = set()
if _gemini_env:
    GEMINI_CHAT_IDS = {int(x.strip()) for x in _gemini_env.split(",") if x.strip()}

# Configuration for OpenAI-enabled chat IDs
_openai_env = os.getenv("OPENAI_CHAT_IDS")
OPENAI_CHAT_IDS = set()
if _openai_env:
    OPENAI_CHAT_IDS = {int(x.strip()) for x in _openai_env.split(",") if x.strip()}

# Combined set of all allowed chat IDs
ALLOWED_CHAT_IDS = GEMINI_CHAT_IDS | OPENAI_CHAT_IDS

KYIV = ZoneInfo(TZ)
DB_PATH = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("daily-summary-bot")