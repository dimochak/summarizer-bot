import os
from loguru import logger
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

# Bot's special user ID for identifying bot messages
BOT_USER_ID = -1  # Special ID for bot messages
MESSAGES_PER_USER = 10

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


# Configuration for PanBot-enabled chat IDs (sarcastic bot responses)
_panbot_env = os.getenv("PANBOT_CHAT_IDS")
PANBOT_CHAT_IDS = set()
if _panbot_env:
    PANBOT_CHAT_IDS = {int(x.strip()) for x in _panbot_env.split(",") if x.strip()}


# Combined set of all allowed chat IDs
ALLOWED_CHAT_IDS = GEMINI_CHAT_IDS | OPENAI_CHAT_IDS

KYIV = ZoneInfo(TZ)
DATABASE_URL = os.getenv("DATABASE_URL")
LOG_FILENAME = os.path.join("/app/data", "bot.log")

logger.remove()
logger.add(
    LOG_FILENAME,
    level="INFO",
    rotation="00:00",
    retention=7,
    encoding='utf-8',
    format=(
        "<green>[{time:YYYY-MM-DD HH:mm:ss}]</green> "
        "<level>{level: <8}</level> "
        "<cyan>{name}:{function}:{line}</cyan>: "
        "<level>{message}</level>"
    ),
    enqueue=True
)

class InterceptHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            logger.opt(exception=record.exc_info).log(
                level, record.getMessage()
            )

logging.basicConfig(handlers=[InterceptHandler()], level=logging.WARNING, force=True)
logging.captureWarnings(True)

log = logger
log.info("Configuration loaded.")
log.info(f"TZ={TZ}")
log.info(f"GEMINI_CHAT_IDS={GEMINI_CHAT_IDS}")
log.info(f"OPENAI_CHAT_IDS={OPENAI_CHAT_IDS}")
log.info(f"ALLOWED_CHAT_IDS={ALLOWED_CHAT_IDS}")
log.info(f"DATABASE_URL={DATABASE_URL}")
log.info(f"GEMINI_MODEL_NAME={GEMINI_MODEL_NAME}")
log.info(f"OPENAI_MODEL_NAME={OPENAI_MODEL_NAME}")
log.info(f"PANBOT_CHAT_IDS={PANBOT_CHAT_IDS}")
log.info(f"MESSAGES_PER_USER={MESSAGES_PER_USER}")