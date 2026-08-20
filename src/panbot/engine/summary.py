from datetime import datetime, timezone, timedelta
from src.tools.db import db_call, get_last_messages, get_messages_since
import src.tools.config as config
from src.summarizer.summarizer import build_messages_snippet, request_summary, ChatSummary

class SummaryEngine:
    async def get_summary(self, chat_id: int, request_type: str, value: int):
        now = datetime.now(timezone.utc)

        if request_type == "messages":
            rows = await db_call(get_last_messages, chat_id, value)
        else:
            delta = timedelta(minutes=value) if request_type == "minutes" else timedelta(hours=value)
            start_time = int((now - delta).timestamp())
            rows = await db_call(get_messages_since, chat_id, start_time)


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
