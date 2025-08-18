import os
import sys
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

from src.db import init_db
from src.config import TZ, ALLOWED_CHAT_IDS

def main():
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print("TELEGRAM_API_ID/TELEGRAM_API_HASH not set, skipping backfill.")
        return 0

    init_db()

    chat_ids = ALLOWED_CHAT_IDS
    if not chat_ids:
        print("No ALLOWED_CHAT_IDS set in environment. Skipping backfill.")
        return 0

    tz = ZoneInfo(TZ)
    today = datetime.now(tz=tz).date()
    date_str = today.strftime("%Y-%m-%d")

    print(f"Backfilling {date_str} for {len(chat_ids)} chats from ALLOWED_CHAT_IDS...")
    for cid in chat_ids:
        print(f"  - Chat {cid}: backfill today")
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m", "src.backfill.backfill_today",
                    "--api-id", str(int(api_id)),
                    "--api-hash", api_hash,
                    "--chat-id", str(cid),
                    "--tz", TZ,
                    "--date", date_str,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"    Backfill failed for chat {cid}: {e}. Continuing...")

    print("Backfill step completed.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())