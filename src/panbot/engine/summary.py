from datetime import datetime, timezone, timedelta
from contextlib import closing
from src.tools.db import db
import src.tools.config as config
from src.summarizer.summarizer import build_messages_snippet, get_openai_summary, get_gemini_summary, should_use_openai

class SummaryEngine:
    async def get_summary(self, chat_id: int, request_type: str, value: int):
        now = datetime.now(timezone.utc)
        
        if request_type == "messages":
            rows = self._fetch_last_n_messages(chat_id, value)
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
        
        try:
            if should_use_openai(chat_id):
                summary = get_openai_summary(prompt)
            else:
                summary = get_gemini_summary(prompt)
            return summary
        except Exception as e:
            config.log.error(f"Error in SummaryEngine: {e}")
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
