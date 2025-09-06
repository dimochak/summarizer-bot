import os
import logging
from logging.handlers import TimedRotatingFileHandler
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

LOG_FILENAME = "bot.log"
logger = logging.getLogger("daily-summary-bot")
logger.setLevel(logging.INFO)

handler = TimedRotatingFileHandler(
    LOG_FILENAME,
    when="midnight",
    backupCount=7,
    encoding='utf-8'
)
formatter = logging.Formatter(
    "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)
logger.addHandler(handler)

log = logger
log.info("Configuration loaded.")
log.info("TZ=%s", TZ)
log.info("GEMINI_CHAT_IDS=%s", GEMINI_CHAT_IDS)
log.info("OPENAI_CHAT_IDS=%s", OPENAI_CHAT_IDS)
log.info("ALLOWED_CHAT_IDS=%s", ALLOWED_CHAT_IDS)
log.info("DB_PATH=%s", DB_PATH)
log.info("GEMINI_MODEL_NAME=%s", GEMINI_MODEL_NAME)
log.info("OPENAI_MODEL_NAME=%s", OPENAI_MODEL_NAME)