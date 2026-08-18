from datetime import datetime, timezone, timedelta
from contextlib import closing
from src.tools.db import db
import src.tools.config as config
from src.summarizer.summarizer import build_messages_snippet, request_summary, ChatSummary

class SummaryEngine:
    async def get_summary(self, chat_id: int, request_type: str, value: int):
        now = datetime.now(timezone.utc)
        
        if request_type == "messages":
            rows = self._fetch_last_n_messages(chat_id, value)
        elif request_type == "minutes":
            start_time = int((now - timedelta(minutes=value)).timestamp())
            rows = self._fetch_messages_since(chat_id, start_time)
        else:  # hours
            start_time = int((now - timedelta(hours=value)).timestamp())
            rows = self._fetch_messages_since(chat_id, start_time)
            
        if not rows:
            return "Я б з радістю щось підсумував, але в чаті порожньо, як у вашій голові 🙄"
        
        snippet = build_messages_snippet(rows)
        prompt = (
            f"Зроби дуже короткий, іронічний та саркастичний підсумок того, що відбулося в чаті. "
            f"Ось повідомлення:\n\n{snippet}\n\n"
            f"Підсумок має бути українською мовою, в стилі PanBot."
        )

        # Схема ChatSummary гарантує рядок у полі summary, тож зникла ціла гілка
        # здогадок про те, що саме повернула модель — dict з "summary", список
        # "topics" чи взагалі щось невідоме.
        try:
            result = await request_summary(prompt, chat_id, ChatSummary)
            return result.summary
        except Exception as e:
            config.log.exception(f"Error in SummaryEngine: {e}")
            return "Хотів зробити підсумок, але ваші теревені настільки беззмістовні, що навіть мій ШІ здався 🤖"

    def _fetch_last_n_messages(self, chat_id: int, n: int):
        with closing(db()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """SELECT text, full_name, username, ts_utc, user_id, message_id, reply_to_message_id
                   FROM messages
                   WHERE chat_id = %s
                   ORDER BY ts_utc DESC LIMIT %s""",
                (chat_id, n)
            )
            rows = cur.fetchall()
            return rows[::-1]  # До хронологічного порядку

    def _fetch_messages_since(self, chat_id: int, start_ts: int):
        with closing(db()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """SELECT text, full_name, username, ts_utc, user_id, message_id, reply_to_message_id
                   FROM messages
                   WHERE chat_id = %s AND ts_utc >= %s
                   ORDER BY ts_utc ASC""",
                (chat_id, start_ts)
            )
            return cur.fetchall()
