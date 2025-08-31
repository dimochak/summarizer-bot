# Telegram Summarizer Bot — OpenAI + Gemini + python-telegram-bot + Fly.io

A daily summarizer bot for Telegram group chats. At **23:59 (Europe/Kyiv)** it posts **#Підсумки_дня** (Daily Summary): a list of topics discussed during the previous day with links to the **first message of each topic** and a link to the **initiator** (clickable user link or @username).

---

## Stack & Features
- **Python 3.11+**, **uv** for dependency management
- **python-telegram-bot v21** (uses **JobQueue** for the daily job)
- **OpenAI GPT** and **Google Generative AI (Gemini)** to cluster messages into topics
- **Configurable toxicity levels** (0-9) for different response styles
- **Token-aware message processing** using tiktoken for optimal API usage
- **SQLite** (optional Fly.io volume for persistence across releases)
- Runs in **long-polling** mode

---

## Prerequisites
1. **Telegram Bot Token** from @BotFather
   - Create a bot → `/newbot`
   - **Disable Group Privacy**: `/setprivacy` → pick the bot → **Disable** (so the bot can read all group messages)
   - Add the bot to your chat (giving it admin rights is recommended)
2. **API Keys**:
   - **OpenAI API Key** (for GPT models)
   - **Gemini API Key** (Google Generative AI)
3. **uv** and **Python 3.11+** installed locally
4. (Production) **flyctl** account + CLI

---

## Quick Local Start
> If the repo already has `pyproject.toml` and `src/main.py`, jump to step 3.

1) Clone and setup
```bash
git clone <your-repo> cd xxl-bot-summarizer uv sync
``` 

2) Install additional dependencies (if needed)
```bash 
uv add "python-telegram-bot==21._" "google-generativeai==0._"
"openai>=1.102.0" "tiktoken>=0.11.0" "orjson==3.*"
``` 

3) Create **.env** in the project root (example)
```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...your_token... OPENAI_API_KEY=sk-...your_openai_key... GEMINI_API_KEY=AIza...your_gemini_key... TZ=Europe/Kyiv
# Configure which chats use which AI provider (comma-separated chat IDs):
OPENAI_CHAT_IDS=-1001234567890,-1009876543210 GEMINI_CHAT_IDS=-1001111111111,-1002222222222
# Optional:
OPENAI_MODEL_NAME=gpt-4o-mini GEMINI_MODEL_NAME=gemini-1.5-flash
# If you mount a volume on Fly (see below):
# DB_PATH=/data/bot.db
``` 

4) Run locally
```bash 
uv run python src/main.py
``` 

5) Test in a chat
- In a test group, send several messages and replies
- Run **/summary_now [0-9]** — you should see today's summary with specified toxicity level
- Run **/summary_now** — uses maximum toxicity (level 9) by default
- **/chatid** returns the chat id and shows which AI provider is configured
- Use **/enable_summaries** and **/disable_summaries** to control automatic daily summaries

> Alternatives to get chat id without /chatid:
> - Copy the link to any message in a private supergroup: `https://t.me/c/<internal_id>/<msg_id>` → your `chat_id` is `-100<internal_id>`.
> - Use Bot API: `curl -s "https://api.telegram.org/bot$TOKEN/getUpdates" | jq '.result[].message.chat'` (run `deleteWebhook` first if needed).

---

## Available Commands
- **/summary_now [0-9]** — Generate immediate summary with optional toxicity level (0=friendly, 9=maximum toxicity)
- **/chatid** — Show chat ID and configured AI provider
- **/enable_summaries** — Enable automatic daily summaries
- **/disable_summaries** — Disable automatic daily summaries
- **/status** — Show current configuration status

---

## AI Provider Configuration

The bot supports both **OpenAI** and **Gemini** models. Configure which chats use which provider via environment variables:

- **OPENAI_CHAT_IDS**: Comma-separated list of chat IDs that will use OpenAI (GPT) models
- **GEMINI_CHAT_IDS**: Comma-separated list of chat IDs that will use Gemini models

Each chat can only use one provider. The bot will automatically use the appropriate API based on the chat configuration.

### Toxicity Levels
- **Level 0**: Friendly and positive tone
- **Level 1-3**: Light humor and mild criticism  
- **Level 4-6**: Pronounced sarcasm and sharp criticism
- **Level 7-9**: Maximum toxicity and provocative style

---

## Deploy to Fly.io (Machines)

### 1) Install & log in to flyctl
```bash
# Linux/macOS
curl -L [https://fly.io/install.sh](https://fly.io/install.sh) | sh fly auth login
``` 

### 2) Dockerfile
The project includes a **uv-based** Dockerfile:
```dockerfile
FROM ghcr.io/astral-sh/uv:python3.11-bookworm 
WORKDIR /app
COPY pyproject.toml uv.lock ./ 
RUN uv sync --frozen --no-dev
COPY . .
CMD ["uv", "run", "python", "src/main.py"]
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
And set the DB path as a secret:
```bash 
fly secrets set DB_PATH=/data/bot.db
``` 

### 5) Secrets & env vars
**Import from `.env`:**
```bash 
fly secrets import < .env
# or set specific ones
fly secrets set TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
TZ=Europe/Kyiv
OPENAI_CHAT_IDS=-100...
GEMINI_CHAT_IDS=-100...
``` 
> Updating secrets triggers a rolling restart automatically.

### 6) Deploy
```bash
fly deploy
```
Verify:
``` bash
fly status
fly logs
```
## Update / Redeploy
- Code or Dockerfile changed: `fly deploy`
- Secrets/env only: `fly secrets import < .env` _(auto-restart)_
- Restart current release without rebuilding: `fly apps restart <app-name>`

---
## Test Checklist
- `/chatid` in the production chat returns a negative id (`-100…` for supergroups) and shows the correct AI provider
- produces a friendly summary `/summary_now 0`
- `/summary_now 9` produces a highly toxic summary
- produces a summary with clickable links to the first message and to the initiator `/summary_now`
- A post arrives at **00:00 Europe/Kyiv** (if summaries are enabled) **#Підсумки_дня**
- BotFather **Group Privacy = Disabled**
- Both OpenAI and Gemini chats work correctly

---
## Tips & Gotchas
- **Multiple AI providers**: Each chat must be configured for exactly one AI provider (OpenAI or Gemini)
- **Token limits**: The bot uses tiktoken to efficiently manage token usage and stay within API limits
- **Private supergroups**: message links look like `https://t.me/c/<internal_id>/<msg_id>` and work for chat members
- **HTML escaping**: Names/titles/summaries are automatically escaped to avoid broken markup
- **Event loop**: Uses **JobQueue** from PTB to avoid event-loop conflicts
- **JSON responses**: Both providers are configured for JSON output with appropriate fallbacks
- **Safety filters**: If high toxicity levels are blocked, the bot automatically retries with lower levels

---
## Troubleshooting
- `This event loop is already running` → don't call `run_polling()` inside `asyncio.run(...)`; use the provided structure `src/main.py`
- `no running event loop` with schedulers → the project uses PTB JobQueue (see ) `src/scheduler.py`
- `getUpdates conflict` → run `deleteWebhook` before `getUpdates` to fetch chat id
- No midnight summary → check TZ, JobQueue schedule, summaries enabled via `/enable_summaries`, and that privacy is **disabled**
- API errors → check that the chat is configured in the correct `*_CHAT_IDS` environment variable
- Safety blocks → try lower toxicity levels or check API quotas/content policies
