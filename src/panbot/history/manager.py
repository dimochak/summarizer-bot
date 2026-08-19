from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
import tiktoken
from datetime import datetime, timezone, timedelta
from src.tools.db import db
import src.tools.config as config

try:
    _encoder = tiktoken.encoding_for_model(config.OPENAI_MODEL_NAME)
except KeyError:
    _encoder = tiktoken.get_encoding("cl100k_base")

class ChatContextHistory(BaseChatMessageHistory):
    """
    Історія повідомлень, яка поєднує:
    1. Останні повідомлення чату (загальний контекст).
    2. Історію безпосереднього діалогу з ботом (якщо є).
    """

    def __init__(self, chat_id: int, current_message_id: int, reply_to_id: int = None, max_tokens: int = 20000):
        self.chat_id = chat_id
        self.current_message_id = current_message_id
        self.reply_to_id = reply_to_id
        self.max_tokens = max_tokens
        self._messages = []

    @property
    def messages(self) -> list[BaseMessage]:
        # Ланцюжок реплаїв тягнемо першим і БЕЗ часових меж: коли користувач явно
        # відповідає на повідомлення, він хоче говорити саме про нього, скільки б
        # днів тому воно не було. Вікно обмежує лише загальний фон чату.
        thread_rows = self._fetch_thread_rows()

        if thread_rows:
            rows_for_budget = thread_rows
        else:
            # Загальний фон беремо тільки тоді, коли треду немає. Раніше цей запит
            # виконувався завжди й тягнув до 2000 рядків, які потім викидались.
            now = datetime.now(timezone.utc)
            start_time = int((now - timedelta(hours=48)).timestamp())
            end_time = int(now.timestamp())
            rows_for_budget = self._fetch_messages(start_time, end_time)

        if not rows_for_budget:
            return []

        messages = []
        used_tokens = 0

        for row in rows_for_budget:  # від найновіших до найстаріших
            name = row["full_name"] or row["username"] or "Учасник"
            text = (row["text"] or "").strip()
            if not text:
                continue

            if row["user_id"] == config.BOT_USER_ID:
                content = text
                msg = AIMessage(content=content)
            else:
                content = f"{name}: {text}"
                msg = HumanMessage(content=content)

            tokens = len(_encoder.encode(content))
            if used_tokens + tokens > self.max_tokens:
                break

            messages.append((row["ts_utc"], msg))
            used_tokens += tokens

        messages.sort(key=lambda item: item[0])
        return [msg for _, msg in messages]

    def _fetch_thread_rows(self) -> list[dict]:
        if not self.reply_to_id:
            return []

        chain = self._fetch_reply_chain(self.reply_to_id, max_depth=25)
        if not chain:
            return []

        bot_index = next(
            (idx for idx, row in enumerate(chain) if row["user_id"] == config.BOT_USER_ID),
            None,
        )
        if bot_index is not None:
            return chain[: bot_index + 1]

        return chain[:1]

    def _fetch_reply_chain(self, message_id: int, max_depth: int = 25) -> list[dict]:
        """Ланцюжок повідомлень угору за reply_to_message_id.

        Один рекурсивний CTE замість циклу з окремим запитом на кожен крок:
        раніше це було до 25 послідовних звернень до БД на одну відповідь бота,
        кожне зі своїм TCP-з'єднанням.
        """
        try:
            with db() as conn, conn.cursor() as cur:
                cur.execute(
                    """WITH RECURSIVE chain AS (
                           SELECT text, full_name, username, ts_utc, user_id,
                                  message_id, reply_to_message_id, 1 AS depth
                           FROM messages
                           WHERE chat_id = %(chat_id)s AND message_id = %(message_id)s
                           UNION ALL
                           SELECT m.text, m.full_name, m.username, m.ts_utc, m.user_id,
                                  m.message_id, m.reply_to_message_id, c.depth + 1
                           FROM messages m
                           JOIN chain c ON m.message_id = c.reply_to_message_id
                           WHERE m.chat_id = %(chat_id)s AND c.depth < %(max_depth)s
                       )
                       SELECT text, full_name, username, ts_utc, user_id,
                              message_id, reply_to_message_id
                       FROM chain
                       ORDER BY depth""",
                    {
                        "chat_id": self.chat_id,
                        "message_id": message_id,
                        "max_depth": max_depth,
                    },
                )
                return list(cur.fetchall())
        except Exception as e:
            config.log.error(f"Error fetching reply chain from DB: {e}")
            return []

    def _fetch_messages(self, start_ts: int, end_ts: int):
        try:
            with db() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT text, full_name, username, ts_utc, user_id, message_id, reply_to_message_id
                       FROM messages
                       WHERE chat_id = %s
                         AND ts_utc >= %s
                         AND ts_utc <= %s
                         AND message_id != %s
                       ORDER BY ts_utc DESC LIMIT 2000""",
                    (self.chat_id, start_ts, end_ts, self.current_message_id)
                )
                return cur.fetchall()
        except Exception as e:
            config.log.error(f"Error fetching history from DB: {e}")
            return []

    def add_message(self, message: BaseMessage) -> None:
        pass

    def clear(self) -> None:
        pass
