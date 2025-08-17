import os
import re
import logging
import sqlite3
import orjson as json
from html import escape
from datetime import datetime, timedelta, time as dtime, timezone
from contextlib import closing
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from telegram import Update, Chat, Message
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, ContextTypes,
    MessageHandler, CommandHandler, filters
)

import google.generativeai as genai

# ---------- Config ----------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TZ = os.getenv("TZ", "Europe/Kyiv")
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0"))
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-1.5-flash")

KIEV = ZoneInfo(TZ)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("daily-summary-bot")

# ---------- DB ----------
DB_PATH = "bot.db"
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
"""

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

with closing(db()) as conn, conn, closing(conn.cursor()) as cur:
    for stmt in SCHEMA.strip().split(";\n\n"):
        if stmt.strip():
            cur.execute(stmt)

# ---------- Helpers ----------
def utc_ts(dt: datetime) -> int:
    return int(dt.timestamp())

def local_midnight_bounds(day_local: datetime):
    # Межі попереднього дня у локальному TZ
    day = day_local.astimezone(KIEV).date()
    start_local = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=KIEV)
    end_local   = datetime.combine(day,               datetime.min.time(), tzinfo=KIEV)
    return start_local, end_local

def message_link(chat: Chat, message_id: int) -> str:
    """
    Створює лінк на повідомлення:
    - публічний супергруп/канал: https://t.me/<username>/<message_id>
    - приватний супергруп: https://t.me/c/<abs(chat_id_wo_-100)>/<message_id>
      (працює для учасників чату)
    """
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    cid = str(chat.id)
    if cid.startswith("-100"):
        cid = cid[4:]
    else:
        cid = cid.lstrip("-")
    return f"https://t.me/c/{cid}/{message_id}"

def user_link(user_id: int, username: str | None, full_name: str) -> str:
    # Якщо є username — лінк на @username, інакше deep-link на user id
    label = escape(full_name or (username and f"@{username}") or "Користувач")
    if username:
        return f'<a href="https://t.me/{escape(username)}">{label}</a>'
    return f'<a href="tg://user?id={user_id}">{label}</a>'

def clean_text(s: str | None) -> str:
    if not s:
        return ""
    return s.strip()

# ---------- Gemini ----------
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    MODEL_NAME,
    generation_config={"response_mime_type": "application/json"}
)

SUMMARIZE_PROMPT = """Ти — помічник, що групує повідомлення чату у теми за календарний день.
Завдання:
1) Зкластеризуй повідомлення у 2–10 тем.
2) Для кожної теми визнач:
   - short_title: ≤7 слів, змістовна назва
   - first_message_id: message_id першого (найранішого) повідомлення в темі
   - initiator_user_id: user_id автора першого повідомлення теми
   - summary: 1–2 речення підсумку (українською, без імен)
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
    """
    Формує компактний перелік:
    [HH:MM] <name> (uid=..., mid=..., reply_to=...): text
    Обрізає по кількості символів для стабільності промпту.
    """
    lines = []
    for r in rows:
        ts = datetime.fromtimestamp(r["ts_utc"], tz=ZoneInfo("UTC")).astimezone(KIEV)
        time = ts.strftime("%H:%M")
        name = r["full_name"] or (r["username"] and f"@{r['username']}") or f"id{r['user_id']}"
        frag = (r["text"] or "").replace("\n", " ").strip()
        if len(frag) > 240:
            frag = frag[:240] + "…"
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
        log.exception("Gemini summary error: %s", e)
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

# ---------- Telegram Handlers ----------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.effective_message
    chat: Chat = update.effective_chat

    if ALLOWED_CHAT_ID and chat.id != ALLOWED_CHAT_ID:
        return

    text = msg.text or msg.caption
    if text is None:
        return

    ts = msg.date
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    with closing(db()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """INSERT OR IGNORE INTO messages
               (chat_id, message_id, user_id, username, full_name, text, reply_to_message_id, ts_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat.id,
                msg.message_id,
                (msg.from_user and msg.from_user.id) or None,
                (msg.from_user and msg.from_user.username) or None,
                (msg.from_user and msg.from_user.full_name) or None,
                text,
                (msg.reply_to_message and msg.reply_to_message.message_id) or None,
                utc_ts(ts.astimezone(timezone.utc))
            )
        )
        conn.commit()

async def send_daily_summary(app: Application):
    chat_id = ALLOWED_CHAT_ID
    if not chat_id:
        log.warning("ALLOWED_CHAT_ID не задано — підсумки не будуть надіслані.")
        return
    try:
        chat = await app.bot.get_chat(chat_id)
    except Exception as e:
        log.exception("Cannot get chat: %s", e)
        return

    now_local = datetime.now(tz=KIEV)
    start_local, end_local = local_midnight_bounds(now_local)
    text = await summarize_day(chat, start_local, end_local, None)
    if not text:
        text = f"<b>#Підсумки_дня — {start_local.date():%d.%m.%Y}</b>\n\nНемає повідомлень або не вдалося сформувати підсумок."
    await app.bot.send_message(chat_id=chat.id, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ---------- JobQueue wrapper ----------
async def send_daily_summary_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    await send_daily_summary(app)

def schedule_daily(app: Application):
    app.job_queue.run_daily(
        send_daily_summary_job,
        time=dtime(0, 0, tzinfo=KIEV),
        name="daily_summary"
    )
    log.info("Daily job scheduled for 00:00 %s", TZ)

# ---------- Commands ----------
async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html(f"<code>{update.effective_chat.id}</code>")

async def cmd_summary_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    now_local = datetime.now(tz=KIEV)
    start_local = datetime.combine(now_local.date(), dtime.min, tzinfo=KIEV)  # сьогодні від 00:00
    text = await summarize_day(chat, start_local, now_local, context)
    if not text:
        text = "<b>#Підсумки_дня — сьогодні</b>\n\nПоки що немає даних або нічого не згрупувалося."
    await update.effective_message.reply_html(text, disable_web_page_preview=True)

# ---------- Main ----------
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(~filters.StatusUpdate.ALL & ~filters.COMMAND, on_message))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("summary_now", cmd_summary_now))

    schedule_daily(app)  # JobQueue працює в тому ж event loop, що й бот

    log.info("Bot started.")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
