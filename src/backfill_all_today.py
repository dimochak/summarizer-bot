import os
import sys
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "bot.db")
TZ = os.getenv("TZ", "Europe/Kyiv")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_enabled_chat_ids():
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT chat_id FROM chats WHERE enabled=1")
        return [row["chat_id"] for row in cur.fetchall()]

def ensure_schema_exists():
    # Ensure we can import main from src/ to initialize schema without starting the bot.
    # main.py guards execution with if __name__ == "__main__": so import is safe.
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    import main  # noqa: F401  # triggers schema creation at import time

def main():
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print("TELEGRAM_API_ID/TELEGRAM_API_HASH not set, skipping backfill.")
        return 0

    ensure_schema_exists()

    chat_ids = get_enabled_chat_ids()
    if not chat_ids:
        print("No enabled chats to backfill. Skipping.")
        return 0

    tz = ZoneInfo(TZ)
    today = datetime.now(tz=tz).date()
    date_str = today.strftime("%Y-%m-%d")

    print(f"Backfilling {date_str} for {len(chat_ids)} enabled chats...")
    backfill_script = SCRIPT_DIR / "backfill_today.py"
    for cid in chat_ids:
        print(f"  - Chat {cid}: backfill today")
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(backfill_script),
                    "--api-id", str(int(api_id)),
                    "--api-hash", api_hash,
                    "--chat-id", str(cid),
                    "--tz", TZ,
                    "--date", date_str,
                ],
                check=True,
                cwd=str(PROJECT_ROOT),
            )
        except subprocess.CalledProcessError as e:
            print(f"    Backfill failed for chat {cid}: {e}. Continuing...")

    print("Backfill step completed.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())