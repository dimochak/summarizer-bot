import os
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from telegram import Chat
from datetime import datetime, timedelta

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
    enabled INTEGER NOT NULL DEFAULT 0,
    custom_role TEXT
);

CREATE TABLE IF NOT EXISTS panbot_limits (
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, chat_id, date)
);

CREATE INDEX IF NOT EXISTS idx_panbot_limits_date ON panbot_limits(date);
    
CREATE TABLE IF NOT EXISTS photo_messages (
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    ts_utc BIGINT NOT NULL,
    file_unique_id TEXT,
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_photo_messages_chat_ts ON photo_messages(chat_id, ts_utc);
    
CREATE TABLE IF NOT EXISTS user_traits (
    user_id BIGINT PRIMARY KEY,
    traits_json JSONB NOT NULL,
    updated_at_utc BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_traits_updated ON user_traits(updated_at_utc);
"""


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Пул з'єднань, створюваний лениво при першому зверненні."""
    global _pool
    if _pool is None:
        assert config.DATABASE_URL, "DATABASE_URL must be set to use Postgres"
        _pool = ConnectionPool(
            config.DATABASE_URL,
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    """Закриває пул. Потрібно тестам і коректному завершенню процесу."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def db():
    """З'єднання з пулу.

    Раніше кожен виклик відкривав НОВЕ з'єднання з Postgres. На одну відповідь
    бота припадало близько 30 конектів: is_bot_message, get_custom_role,
    get_user_traits, вибірка історії, ланцюжок реплаїв і два add_message.

    Транзакція комітиться на виході з блоку, тож явні conn.commit() всередині
    лишаються коректними, але вже не обов'язкові.
    """
    with get_pool().connection() as conn:
        yield conn


def init_db():
    with db() as conn, conn.cursor() as cur:
        # Спершу базова схема, потім міграції: ALTER не має сенсу до CREATE TABLE.
        statements = [stmt.strip() for stmt in SCHEMA.split(';') if stmt.strip()]
        for stmt in statements:
            cur.execute(stmt)

        # Міграції для БД, створених до появи цих колонок.
        # Помилку тут НЕ глушимо: мовчазний `except: pass` означав, що бот
        # стартував з несумісною схемою і падав пізніше на незрозумілому запиті.
        migrations = [
            "ALTER TABLE chats ADD COLUMN IF NOT EXISTS custom_role TEXT",
            "ALTER TABLE photo_messages ADD COLUMN IF NOT EXISTS file_unique_id TEXT",
            # photo_messages.file_id більше не пишеться (його читав лише petfinder),
            # але в наявних БД колонка має NOT NULL і без цього INSERT впав би.
            # Дані не чіпаємо — знімаємо лише обмеження. Умова обов'язкова: на
            # чистій БД колонки вже немає, і беззастережний ALTER зупинив би старт.
            """DO $$
               BEGIN
                   IF EXISTS (SELECT 1 FROM information_schema.columns
                              WHERE table_name = 'photo_messages'
                                AND column_name = 'file_id') THEN
                       ALTER TABLE photo_messages ALTER COLUMN file_id DROP NOT NULL;
                   END IF;
               END $$""",
        ]
        for stmt in migrations:
            try:
                cur.execute(stmt)
            except Exception:
                config.log.exception("Міграція не застосувалась: %s", stmt)
                raise
        conn.commit()
    enable_daily_summaries_for_all_allowed_chats()

def add_message(
    chat_id, message_id, user_id, username, full_name, text, reply_to_message_id, ts_utc
):
    with db() as conn, conn.cursor() as cur:
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
    with db() as conn, conn.cursor() as cur:
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
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT chat_id FROM chats WHERE enabled=1")
        return [r["chat_id"] for r in cur.fetchall()]


def get_panbot_usage(user_id: int, chat_id: int, date: str) -> int:
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count FROM panbot_limits WHERE user_id=%s AND chat_id=%s AND date=%s",
            (user_id, chat_id, date),
        )
        row = cur.fetchone()
        return row["count"] if row else 0


def increment_panbot_usage(user_id: int, chat_id: int, date: str) -> int:
    with db() as conn, conn.cursor() as cur:
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


def is_bot_message(chat_id: int, message_id: int) -> bool:
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM messages WHERE chat_id=%s AND message_id=%s",
            (chat_id, message_id),
        )
        row = cur.fetchone()
        return row is not None and row["user_id"] == config.BOT_USER_ID

def upsert_photo_message(chat_id: int, message_id: int, ts_utc: int, file_unique_id: str | None = None):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO photo_messages (chat_id, message_id, ts_utc, file_unique_id)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (chat_id, message_id)
               DO UPDATE SET
               ts_utc=EXCLUDED.ts_utc,
               file_unique_id=EXCLUDED.file_unique_id""",
            (chat_id, message_id, ts_utc, file_unique_id),
        )
        conn.commit()


def get_duplicate_photo_message_id(chat_id: int, file_unique_id: str, exclude_message_id: int | None = None) -> int | None:
    if not file_unique_id:
        return None
    with db() as conn, conn.cursor() as cur:
        query = "SELECT message_id FROM photo_messages WHERE chat_id=%s AND file_unique_id=%s"
        params = [chat_id, file_unique_id]
        if exclude_message_id:
            query += " AND message_id != %s"
            params.append(exclude_message_id)
        query += " ORDER BY ts_utc ASC LIMIT 1"
        cur.execute(query, params)
        row = cur.fetchone()
        return row["message_id"] if row else None

def upsert_user_traits(user_id: int, traits_json: dict, updated_at_utc: int):
    import json as _json
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO user_traits (user_id, traits_json, updated_at_utc)
               VALUES (%s, %s::jsonb, %s)
               ON CONFLICT (user_id)
               DO UPDATE SET traits_json=EXCLUDED.traits_json,
                             updated_at_utc=EXCLUDED.updated_at_utc""",
            (user_id, _json.dumps(traits_json), updated_at_utc),
        )
        conn.commit()


def get_custom_role(chat_id: int) -> str | None:
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT custom_role FROM chats WHERE chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row["custom_role"] if row else None


def set_custom_role(chat_id: int, role: str | None):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE chats SET custom_role=%s WHERE chat_id=%s",
            (role, chat_id),
        )
        conn.commit()


def get_user_traits(user_id: int) -> dict | None:
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT traits_json FROM user_traits WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        return row["traits_json"] if row else None


def _get_last_user_messages(user_id: int, limit: int = 500) -> list[dict]:
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT chat_id, message_id, ts_utc, username, full_name, text
               FROM messages
               WHERE user_id=%s AND text IS NOT NULL
               ORDER BY ts_utc DESC
               LIMIT %s""",
            (user_id, limit),
        )
        return list(cur.fetchall())


def cleanup_old_data(days: int):
    now_kyiv = datetime.now(config.KYIV)
    cutoff_dt = now_kyiv - timedelta(days=days)
    
    cutoff_ts = int(cutoff_dt.timestamp())
    # date in panbot_limits is stored as TEXT 'YYYY-MM-DD'
    cutoff_date = cutoff_dt.strftime("%Y-%m-%d")

    with db() as conn, conn.cursor() as cur:
        # Cleanup messages
        cur.execute("DELETE FROM messages WHERE ts_utc < %s", (cutoff_ts,))
        deleted_messages = cur.rowcount

        # Cleanup pet photos
        # Cleanup photo messages
        cur.execute("DELETE FROM photo_messages WHERE ts_utc < %s", (cutoff_ts,))
        deleted_photos = cur.rowcount

        # Cleanup panbot limits
        cur.execute("DELETE FROM panbot_limits WHERE date < %s", (cutoff_date,))
        deleted_limits = cur.rowcount

        conn.commit()

    config.log.info(
        f"Database cleanup completed (retention: {days} days). "
        f"Deleted: {deleted_messages} messages, "
        f"{deleted_photos} photo messages, {deleted_limits} limit records."
    )