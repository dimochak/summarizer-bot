import os
import tempfile

# src.tools.config reads these at import time, so they must be set before any
# `src` module is imported by the test suite.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("TZ", "Europe/Kyiv")
# Avoid the production /app/data log path (read-only outside the Fly container).
os.environ.setdefault("LOG_FILE", os.path.join(tempfile.gettempdir(), "summarizer-test.log"))
