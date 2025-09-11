from contextlib import closing
import psycopg
from psycopg.rows import dict_row
from telegram import Chat

import src.tools.config as config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    user_id BIGINT,
    username TEXT,
    full_name TEXT,
    text TEXT,
    reply_to_message_id BIGINT,
    ts_utc BIGINT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts_utc);

CREATE TABLE IF NOT EXISTS chats (
    chat_id BIGINT PRIMARY KEY,
    title TEXT,
    enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS panbot_limits (
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, chat_id, date)
);

CREATE INDEX IF NOT EXISTS idx_panbot_limits_date ON panbot_limits(date);
"""


def db():
    assert config.DATABASE_URL, "DATABASE_URL must be set to use Postgres"
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)


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
            """INSERT INTO messages
               (chat_id, message_id, user_id, username, full_name, text, reply_to_message_id, ts_utc)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (chat_id, message_id) DO NOTHING""",
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
    title = chat.title or chat.username or str(chat.id)
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "INSERT INTO chats(chat_id, title, enabled) VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO NOTHING",
            (chat.id, title, enable_default),
        )
        cur.execute(
            "UPDATE chats SET title=%s WHERE chat_id=%s AND (title IS NULL OR title<>%s)",
            (title, chat.id, title),
        )
        conn.commit()



def enable_daily_summaries_for_all_allowed_chats():
    with db() as conn:
        cur = conn.cursor()
        for chat_id in config.ALLOWED_CHAT_IDS:
            cur.execute("SELECT enabled FROM chats WHERE chat_id=%s", (chat_id,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO chats (chat_id, enabled) VALUES (%s, 1) ON CONFLICT (chat_id) DO NOTHING",
                    (chat_id,),
                )
                config.log.info(f"Inserted chat_id {chat_id} with enabled=1 in chats table")
            else:
                if row["enabled"] != 1:
                    cur.execute("UPDATE chats SET enabled=1 WHERE chat_id=%s", (chat_id,))
                    config.log.info(f"Updated chat_id {chat_id} to enabled=1 in chats table")
        conn.commit()


def get_enabled_chat_ids() -> list[int]:
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT chat_id FROM chats WHERE enabled=1")
        return [r["chat_id"] for r in cur.fetchall()]


def get_panbot_usage(user_id: int, chat_id: int, date: str) -> int:
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT count FROM panbot_limits WHERE user_id=%s AND chat_id=%s AND date=%s",
            (user_id, chat_id, date),
        )
        row = cur.fetchone()
        return row["count"] if row else 0


def increment_panbot_usage(user_id: int, chat_id: int, date: str) -> int:
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """INSERT INTO panbot_limits (user_id, chat_id, date, count)
               VALUES (%s, %s, %s, 1)
               ON CONFLICT (user_id, chat_id, date)
               DO UPDATE SET count = panbot_limits.count + 1
               RETURNING count""",
            (user_id, chat_id, date),
        )
        new_count = cur.fetchone()["count"]
        conn.commit()
        return new_count


def reset_panbot_usage_for_date(date: str):
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute("DELETE FROM panbot_limits WHERE date=%s", (date,))
        conn.commit()


def is_bot_message(chat_id: int, message_id: int) -> bool:
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT user_id FROM messages WHERE chat_id=%s AND message_id=%s",
            (chat_id, message_id),
        )
        row = cur.fetchone()
        return row is not None and row["user_id"] == config.BOT_USER_ID
