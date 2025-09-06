import sqlite3
from contextlib import closing
from telegram import Chat

import src.tools.config as config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER,
    username TEXT,
    full_name TEXT,
    text TEXT,
    reply_to_message_id INTEGER,
    ts_utc INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts_utc);

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    enabled INTEGER NOT NULL DEFAULT 0
);
    
CREATE TABLE IF NOT EXISTS panbot_limits (
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, chat_id, date)
);

CREATE INDEX IF NOT EXISTS idx_panbot_limits_date ON panbot_limits(date);
"""


def db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn, conn, closing(conn.cursor()) as cur:
        statements = [stmt.strip() for stmt in SCHEMA.split(';') if stmt.strip()]

        for stmt in statements:
            cur.execute(stmt)

    enable_daily_summaries_for_all_allowed_chats()

def add_message(
    chat_id, message_id, user_id, username, full_name, text, reply_to_message_id, ts_utc
):
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """INSERT OR IGNORE INTO messages
               (chat_id, message_id, user_id, username, full_name, text, reply_to_message_id, ts_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat_id,
                message_id,
                user_id,
                username,
                full_name,
                text,
                reply_to_message_id,
                ts_utc,
            ),
        )
        conn.commit()


def ensure_chat_record(chat: Chat, *, enable_default: int = 1):
    """Створюємо/оновлюємо запис про чат у таблиці chats."""
    title = chat.title or chat.username or str(chat.id)
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "INSERT OR IGNORE INTO chats(chat_id, title, enabled) VALUES (?, ?, ?)",
            (chat.id, title, enable_default),
        )
        # оновлюємо title, якщо змінився
        cur.execute(
            "UPDATE chats SET title=? WHERE chat_id=? AND (title IS NULL OR title!=?)",
            (title, chat.id, title),
        )
        conn.commit()


def enable_daily_summaries_for_all_allowed_chats():
    """
    Ensure all ALLOWED_CHAT_IDS have daily summaries enabled by default after deployment.
    This function is idempotent and safe to run on every startup.
    """
    with db() as conn:
        cur = conn.cursor()
        for chat_id in config.ALLOWED_CHAT_IDS:
            cur.execute("SELECT enabled FROM chats WHERE chat_id=?", (chat_id,))
            row = cur.fetchone()
            if row is None:
                # Insert with enabled=1
                cur.execute(
                    "INSERT INTO chats (chat_id, enabled) VALUES (?, 1)",
                    (chat_id,),
                )
                config.log.info(f"Inserted chat_id {chat_id} with enabled=1 in chats table")
            elif row["enabled"] != 1:
                cur.execute(
                    "UPDATE chats SET enabled=1 WHERE chat_id=?",
                    (chat_id,),
                )
                config.log.info(f"Updated chat_id {chat_id} to enabled=1 in chats table")
        conn.commit()



def get_enabled_chat_ids() -> list[int]:
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT chat_id FROM chats WHERE enabled=1")
        return [r[0] for r in cur.fetchall()]


def get_panbot_usage(user_id: int, chat_id: int, date: str) -> int:
    """Get current usage count for user on specific date"""
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT count FROM panbot_limits WHERE user_id=? AND chat_id=? AND date=?",
            (user_id, chat_id, date)
        )
        row = cur.fetchone()
        return row["count"] if row else 0


def increment_panbot_usage(user_id: int, chat_id: int, date: str) -> int:
    """Increment usage count for user and return new count"""
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """INSERT OR REPLACE INTO panbot_limits (user_id, chat_id, date, count)
               VALUES (?, ?, ?, COALESCE(
                   (SELECT count FROM panbot_limits WHERE user_id=? AND chat_id=? AND date=?) + 1,
                   1
               ))""",
            (user_id, chat_id, date, user_id, chat_id, date)
        )
        conn.commit()

        # Get the new count
        cur.execute(
            "SELECT count FROM panbot_limits WHERE user_id=? AND chat_id=? AND date=?",
            (user_id, chat_id, date)
        )
        return cur.fetchone()["count"]


def reset_panbot_usage_for_date(date: str):
    """Reset all usage counts for a specific date (for testing)"""
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("DELETE FROM panbot_limits WHERE date=?", (date,))
        conn.commit()


def is_bot_message(chat_id: int, message_id: int) -> bool:
    """Check if a message was sent by the bot"""
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT user_id FROM messages WHERE chat_id=? AND message_id=?",
            (chat_id, message_id)
        )
        row = cur.fetchone()
        return row is not None and row["user_id"] == config.BOT_USER_ID
