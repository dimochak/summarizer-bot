from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
import tiktoken
from datetime import datetime, timezone, timedelta
from contextlib import closing
from src.tools.db import db, get_message_by_id
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
        # Визначаємо часові межі
        now = datetime.now(timezone.utc)
        start_time = int((now - timedelta(hours=48)).timestamp())
        end_time = int(now.timestamp())

        general_rows = self._fetch_messages(start_time, end_time)
        thread_rows = self._fetch_thread_rows(start_time, end_time)

        if not general_rows and not thread_rows:
            return []

        messages = []
        used_tokens = 0

        if thread_rows:
            rows_for_budget = thread_rows
        else:
            rows_for_budget = general_rows

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

    def _fetch_thread_rows(self, start_ts: int, end_ts: int) -> list[dict]:
        if not self.reply_to_id:
            return []

        chain = self._fetch_reply_chain(self.reply_to_id, start_ts, end_ts, max_depth=25)
        if not chain:
            return []

        bot_index = next(
            (idx for idx, row in enumerate(chain) if row["user_id"] == config.BOT_USER_ID),
            None,
        )
        if bot_index is not None:
            return chain[: bot_index + 1]

        return chain[:1]

    def _fetch_reply_chain(self, message_id: int, start_ts: int, end_ts: int, max_depth: int = 25) -> list[dict]:
        chain = []
        seen_ids = set()
        current_id = message_id

        while current_id and current_id not in seen_ids and len(chain) < max_depth:
            row = get_message_by_id(self.chat_id, current_id)
            if not row:
                break

            seen_ids.add(current_id)
            if row["ts_utc"] < start_ts or row["ts_utc"] > end_ts:
                break

            chain.append(row)
            current_id = row.get("reply_to_message_id")

        return chain

    def _fetch_messages(self, start_ts: int, end_ts: int):
        try:
            with closing(db()) as conn, closing(conn.cursor()) as cur:
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
