# Telegram Summarizer Bot — Gemini + python-telegram-bot + Fly.io

A daily summarizer bot for Telegram group chats. At **23:59 (Europe/Kyiv)** it posts **#Підсумки_дня** (Daily Summary): a list of topics discussed during the previous day with links to the **first message of each topic** and a link to the **initiator** (clickable user link or @username).

---

## Stack & Features
- **Python 3.11+**, **uv** for dependency management
- **python-telegram-bot v21** (uses **JobQueue** for the daily job)
- **Google Generative AI (Gemini)** to cluster messages into topics
- **SQLite** (optional Fly.io volume for persistence across releases)
- Runs in **long-polling** mode

---

## Prerequisites
1. **Telegram Bot Token** from @BotFather
   - Create a bot → `/newbot`
   - **Disable Group Privacy**: `/setprivacy` → pick the bot → **Disable** (so the bot can read all group messages)
   - Add the bot to your chat (giving it admin rights is recommended)
2. **Gemini API Key** (Google Generative AI)
3. **uv** and **Python 3.11+** installed locally
4. (Production) **flyctl** account + CLI

---

## Quick Local Start
> If the repo already has `pyproject.toml` and `main.py`, jump to step 3.

1) Initialize project & environment
```bash
uv init telegram-summarizer-bot
cd telegram-summarizer-bot
uv venv --python 3.11
```

2) Install dependencies
```bash
uv add "python-telegram-bot==21.*" "google-generativeai==0.*" \
       "python-dotenv==1.*" "orjson==3.*"
```

3) Create **.env** in the project root (example)
```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...your_token...
GEMINI_API_KEY=AIza...your_gemini_key...
TZ=Europe/Kyiv
# set the chat id after you obtain it (see below):
ALLOWED_CHAT_ID=-1001234567890
# Optional:
MODEL_NAME=gemini-1.5-flash
# If you mount a volume on Fly (see below):
# DB_PATH=/data/bot.db
```

4) Run locally
```bash
uv run python main.py
```

5) Test in a chat
- In a test group, send several messages and replies
- Run **/summary_now** — you should see today’s summary
- **/chatid** returns the chat id → update `ALLOWED_CHAT_ID` in `.env` and restart the bot

> Alternatives to get chat id without /chatid:
> - Copy the link to any message in a private supergroup: `https://t.me/c/<internal_id>/<msg_id>` → your `chat_id` is `-100<internal_id>`.
> - Use Bot API: `curl -s "https://api.telegram.org/bot$TOKEN/getUpdates" | jq '.result[].message.chat'` (run `deleteWebhook` first if needed).

---

## Deploy to Fly.io (Machines)

### 1) Install & log in to flyctl
```bash
# Linux/macOS
curl -L https://fly.io/install.sh | sh
fly auth login
```

### 2) Dockerfile
Recommended **uv-based** image:
```dockerfile
# Dockerfile
FROM ghcr.io/astral-sh/uv:python3.11-bookworm
WORKDIR /app

# Dependencies (locked via uv.lock if present)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

# App code
COPY . .

# Entrypoint
CMD ["uv", "run", "python", "main.py"]
```

### 3) Initialize the Fly app
```bash
fly launch --no-deploy
# pick a region close to Ukraine/Europe (e.g., fra/ams/waw)
```
This creates `fly.toml` (defaults are fine).

### 4) (Optional) Persistent storage for SQLite
By default, the SQLite file inside the container is ephemeral between releases. To persist history:
```bash
fly volumes create data --size 1
```
Add to `fly.toml`:
```toml
[[mounts]]
  source = "data"
  destination = "/data"
```
And in `.env` (or as a secret), set the DB path:
```dotenv
DB_PATH=/data/bot.db
```
> Ensure your code uses `DB_PATH = os.getenv("DB_PATH", "bot.db")`. If it’s hardcoded, update that line.

### 5) Secrets & env vars
**Import from `.env`:**
```bash
fly secrets import < .env
# or set specific ones
fly secrets set TELEGRAM_BOT_TOKEN=... GEMINI_API_KEY=... TZ=Europe/Kyiv ALLOWED_CHAT_ID=-100...
```
> Updating secrets triggers a rolling restart automatically.

### 6) Deploy
```bash
fly deploy
```
Verify:
```bash
fly status
fly logs
```

---

## Update / Redeploy
- Code or Dockerfile changed: `fly deploy`
- Secrets/env only: `fly secrets import < .env` *(auto-restart)*
- Restart current release without rebuilding: `fly apps restart <app-name>`

---

## Test Checklist
- `/chatid` in the production chat returns a negative id (`-100…` for supergroups)
- `/summary_now` produces a summary with clickable links to the first message and to the initiator
- A **#Підсумки_дня** post arrives at **00:00 Europe/Kyiv**
- BotFather **Group Privacy = Disabled**

---

## Tips & Gotchas
- **Private supergroups**: message links look like `https://t.me/c/<internal_id>/<msg_id>` and work for chat members.
- **HTML escaping**: escape names/titles/summaries (`html.escape`) to avoid broken markup.
- **Event loop**: prefer **JobQueue** from PTB over a separate APScheduler to avoid event-loop clashes.
- **Gemini JSON**: set `response_mime_type="application/json"` and keep a fallback for imperfect JSON.

---

## Troubleshooting
- `This event loop is already running` → don’t call `run_polling()` inside `asyncio.run(...)`; use a synchronous `main()` with `run_polling()` or manage the lifecycle manually.
- `no running event loop` with APScheduler → switch to PTB JobQueue (as in this repo).
- `getUpdates conflict` → run `deleteWebhook` before `getUpdates` to fetch chat id.
- No midnight summary → check TZ, JobQueue schedule, and that privacy is **disabled** so the bot sees messages.


---


