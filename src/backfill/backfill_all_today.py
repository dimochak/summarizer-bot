import os
import sys
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# This script is run as a module from the project root,
# so we can import directly from `src`.
from src.db import get_enabled_chat_ids, init_db
from src.config import TZ

def main():
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print("TELEGRAM_API_ID/TELEGRAM_API_HASH not set, skipping backfill.")
        return 0

    init_db()

    chat_ids = get_enabled_chat_ids()
    if not chat_ids:
        print("No enabled chats to backfill. Skipping.")
        return 0

    tz = ZoneInfo(TZ)
    today = datetime.now(tz=tz).date()
    date_str = today.strftime("%Y-%m-%d")

    print(f"Backfilling {date_str} for {len(chat_ids)} enabled chats...")
    for cid in chat_ids:
        print(f"  - Chat {cid}: backfill today")
        try:
            # Run the child script as a module to ensure its sys.path is correct.
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