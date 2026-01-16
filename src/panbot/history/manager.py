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

        rows = self._fetch_messages(start_time, end_time)

        if self.reply_to_id:
            message_ids = {r["message_id"] for r in rows}
            if self.reply_to_id not in message_ids:
                replied_msg = get_message_by_id(self.chat_id, self.reply_to_id)
                if replied_msg:
                    rows.append(replied_msg)
                    rows.sort(key=lambda x: x["ts_utc"], reverse=True)

        if not rows:
            return []

        messages = []
        used_tokens = 0
        
        for row in rows[::-1]:  # Хронологічно
            name = row["full_name"] or row["username"] or "Учасник"
            text = (row["text"] or "").strip()
            if not text:
                continue

            if row["user_id"] == config.BOT_USER_ID:
                msg = AIMessage(content=text)
            else:
                msg = HumanMessage(content=f"{name}: {text}")

            tokens = len(_encoder.encode(text))
            if used_tokens + tokens > self.max_tokens:
                break
                
            messages.append(msg)
            used_tokens += tokens

        return messages

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
