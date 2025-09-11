# src/tools/migrate_sqlite_to_pg.py
import argparse
import sqlite3
from contextlib import closing

import src.tools.config as config
import src.tools.db as dbmod

def fetch_all_sqlite(sqlite_path: str):
    sconn = sqlite3.connect(sqlite_path)
    sconn.row_factory = sqlite3.Row
    with closing(sconn) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT * FROM chats")
        chats = cur.fetchall()
        cur.execute("SELECT * FROM messages")
        messages = cur.fetchall()
        cur.execute("SELECT * FROM panbot_limits")
        limits = cur.fetchall()
    return chats, messages, limits

def migrate(sqlite_path: str):
    assert config.DATABASE_URL, "DATABASE_URL must be set to migrate to Postgres"
    # Ensure schema exists in Postgres
    dbmod.init_db()

    chats, messages, limits = fetch_all_sqlite(sqlite_path)
    print(f"Found: chats={len(chats)}, messages={len(messages)}, panbot_limits={len(limits)}")

    with dbmod.db() as conn, conn.cursor() as cur:
        for r in chats:
            cur.execute(
                "INSERT INTO chats (chat_id, title, enabled) VALUES (%s, %s, %s) "
                "ON CONFLICT (chat_id) DO UPDATE SET title=EXCLUDED.title, enabled=EXCLUDED.enabled",
                (r["chat_id"], r["title"], r["enabled"]),
            )
        for r in messages:
            cur.execute(
                """INSERT INTO messages
                   (chat_id, message_id, user_id, username, full_name, text, reply_to_message_id, ts_utc)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (chat_id, message_id) DO NOTHING""",
                (
                    r["chat_id"], r["message_id"], r["user_id"], r["username"],
                    r["full_name"], r["text"], r["reply_to_message_id"], r["ts_utc"],
                ),
            )
        for r in limits:
            cur.execute(
                """INSERT INTO panbot_limits (user_id, chat_id, date, count)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id, chat_id, date) DO UPDATE SET count=EXCLUDED.count""",
                (r["user_id"], r["chat_id"], r["date"], r["count"]),
            )
        conn.commit()
    print("Migration complete.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", required=True, help="Path to SQLite DB (e.g., /app/data/bot.db)")
    args = parser.parse_args()
    migrate(args.sqlite)

if __name__ == "__main__":
    main()