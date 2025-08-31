import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import User

from src.db import init_db, add_message


def insert_row(r):
    add_message(
        r["chat_id"],
        r["message_id"],
        r["user_id"],
        r["username"],
        r["full_name"],
        r["text"],
        r["reply_to_message_id"],
        r["ts_utc"],
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--api-id", required=True, type=int, help="my.telegram.org → API ID"
    )
    ap.add_argument("--api-hash", required=True, help="my.telegram.org → API HASH")
    ap.add_argument("--chat-id", required=True, help="e.g. -1001234567890")
    ap.add_argument("--tz", default="Europe/Kyiv")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today local)")
    args = ap.parse_args()

    init_db()

    # Get Telethon session credentials from environment variables
    session_string = os.getenv("TELETHON_SESSION_STRING")
    if not session_string:
        print(
            "Error: TELETHON_SESSION_STRING environment variable not set. Cannot backfill.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    tz = ZoneInfo(args.tz)
    if args.date:
        y, m, d = map(int, args.date.split("-"))
        day = datetime(y, m, d, tzinfo=tz)
    else:
        day = datetime.now(tz=tz)
    start_local = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    api_id, api_hash = args.api_id, args.api_hash

    client = TelegramClient(StringSession(session_string), api_id, api_hash)

    async def run():
        entity = await client.get_entity(int(args.chat_id))
        async for msg in client.iter_messages(
            entity, offset_date=start_utc, reverse=True
        ):
            if msg.date is None:
                continue
            # msg.date — aware UTC
            if msg.date < start_utc:
                continue
            if msg.date >= end_utc:
                break

            text = (msg.message or "").strip()
            if not text:
                continue

            user_id = None
            username = None
            full_name = None
            if msg.sender_id:
                user_id = int(getattr(msg, "sender_id", 0) or 0)
                try:
                    s = await msg.get_sender()
                    if isinstance(s, User):
                        username = s.username
                        full_name = (
                            " ".join(filter(None, [s.first_name, s.last_name])).strip()
                            or None
                        )
                except Exception:
                    pass

            row = {
                "chat_id": int(args.chat_id),
                "message_id": int(msg.id),
                "user_id": user_id,
                "username": username,
                "full_name": full_name,
                "text": text,
                "reply_to_message_id": int(getattr(msg, "reply_to_msg_id", 0) or 0)
                or None,
                "ts_utc": int(msg.date.timestamp()),
            }
            insert_row(row)

    with client:
        client.loop.run_until_complete(run())


if __name__ == "__main__":
    main()
