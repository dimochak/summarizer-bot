import re
import orjson as json
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import closing
from html import escape

import google.generativeai as genai
from telegram import Chat
from telegram.ext import ContextTypes

import src.config as config
from src.db import db
from src.utils import utc_ts, clean_text, message_link, user_link

genai.configure(api_key=config.GEMINI_API_KEY)
model = genai.GenerativeModel(
    config.MODEL_NAME,
    generation_config={"response_mime_type": "application/json"}
)

SUMMARIZE_PROMPT = """Ти — помічник, що групує повідомлення чату у теми за календарний день.
Завдання:
1) Зкластеризуй повідомлення у 2–5 тем.
2) Для кожної теми визнач:
   - short_title: ≤7 слів, змістовна назва
   - first_message_id: message_id першого (найранішого) повідомлення в темі
   - initiator_user_id: user_id автора першого повідомлення теми
   - summary: 1–2 речення підсумку. Надай власні коментарі у провокаційному і токсичному стилі. Але, якщо тематика пов'язана з агресією росії проти України, токсичність має бути направлена на агресора.
3) Поверни РІВНО JSON такого вигляду:
{
  "topics": [
    {
      "short_title": "…",
      "first_message_id": 123,
      "initiator_user_id": 456,
      "summary": "…"
    }
  ]
}

УВАГА:
- Орієнтуйся на reply-ланцюжки як ознаку теми; для нереплайних — об’єднуй за змістом.
- Ігноруй службові повідомлення/стікери, якщо вони нічого не додають по суті.
"""

def build_messages_snippet(rows, max_chars: int = 100_000) -> str:
    lines = []
    for r in rows:
        ts = datetime.fromtimestamp(r["ts_utc"], tz=ZoneInfo("UTC")).astimezone(config.KYIV)
        time = ts.strftime("%H:%M")
        name = r["full_name"] or (r["username"] and f"@{r['username']}") or f"id{r['user_id']}"
        frag = (r["text"] or "").replace("\n", " ").strip()
        if len(frag) > 500:
            frag = frag[:500] + "…"
        reply = f", reply_to={r['reply_to_message_id']}" if r["reply_to_message_id"] else ""
        lines.append(f"[{time}] {name} (uid={r['user_id']}, mid={r['message_id']}{reply}): {frag}")
    s = "\n".join(lines)
    return s[:max_chars]

async def summarize_day(chat: Chat, start_local: datetime, end_local: datetime, ctx: ContextTypes.DEFAULT_TYPE) -> str | None:
    start_utc = start_local.astimezone(ZoneInfo("UTC"))
    end_utc   = end_local.astimezone(ZoneInfo("UTC"))
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT * FROM messages WHERE chat_id=? AND ts_utc>=? AND ts_utc<? ORDER BY ts_utc ASC",
            (chat.id, utc_ts(start_utc), utc_ts(end_utc)),
        )
        rows = [dict(r) for r in cur.fetchall()]

    rows = [r for r in rows if clean_text(r["text"])]
    if not rows:
        return None

    snippet = build_messages_snippet(rows)
    prompt = f"""{SUMMARIZE_PROMPT}

Нижче повідомлення за день у форматі рядків:
{snippet}
"""

    try:
        resp = model.generate_content(prompt)
        raw = resp.text or ""
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        topics = data.get("topics", [])
    except Exception as e:
        config.log.exception("Gemini summary error: %s", e)
        return None

    if not topics:
        return None

    day_str = (start_local.date()).strftime("%d.%m.%Y")
    header = f"<b>#Підсумки_дня — {escape(day_str)}</b>"
    items = []

    by_mid = {r["message_id"]: r for r in rows}
    by_uid = {}
    for r in rows:
        by_uid.setdefault(r["user_id"], r)

    for t in topics[:15]:
        title = clean_text(t.get("short_title") or "")
        summ  = clean_text(t.get("summary") or "")
        mid   = t.get("first_message_id")
        uid   = t.get("initiator_user_id")

        if isinstance(mid, int) and mid in by_mid:
            msg_url = message_link(chat, mid)
            title_html = f'<a href="{msg_url}">{escape(title or "Тема")}</a>'
        else:
            title_html = escape(title or "Тема")

        urow = (by_uid.get(uid) or {})
        initiator_html = user_link(
            user_id=urow.get("user_id", uid or 0),
            username=urow.get("username"),
            full_name=urow.get("full_name") or "Учасник"
        )

        line = f"• {title_html} — ініціатор {initiator_html}"
        if summ:
            line += f"\nКоротко: {escape(summ)}"
        items.append(line)

    return header + "\n\n" + "\n\n".join(items)