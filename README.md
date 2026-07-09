# Telegram Summarizer Bot — OpenAI + Gemini + python-telegram-bot + Fly.io

A Ukrainian-language Telegram group bot with three features:

- **Daily Summary** — at **23:59 (Europe/Kyiv)** it posts **#Підсумки_дня**: the day's topics clustered by an LLM, each linking to the **first message of the topic** and to its **initiator** (clickable user link or @username). Also available on demand via `/summary_now`.
- **PanBot** — a sarcastic auto-responder that replies when a message contains a trigger word (`ботяндра`/`ботяндрік`) or is a reply to one of the bot's own messages, subject to a per-user daily quota.
- **PetFinder** — `/petfinder` scans the day's photos, detects cats/dogs with a vision model, and posts links with short ironic captions.

---

## Stack & Features
- **Python 3.13**, **uv** for dependency management
- **python-telegram-bot v21** (uses **JobQueue** for the daily job)
- **OpenAI GPT** and **Google Generative AI (Gemini)** for topic clustering, sarcasm, and image captioning
- **Configurable toxicity levels** (0–9) for the summary style
- **Token-aware message processing** using tiktoken
- **PostgreSQL** for message history, chat settings, quotas, and pet/photo caches
- Runs in **long-polling** mode

---

## Prerequisites
1. **Telegram Bot Token** from @BotFather
   - Create a bot → `/newbot`
   - **Disable Group Privacy**: `/setprivacy` → pick the bot → **Disable** (so the bot can read all group messages)
   - Add the bot to your chat (giving it admin rights is recommended)
2. **API Keys**:
   - **OpenAI API Key** (GPT models + pet image captioning)
   - **Gemini API Key** (Google Generative AI)
3. **A PostgreSQL database** (`DATABASE_URL` connection string)
4. **uv** and **Python 3.13** installed locally
5. (Production) **flyctl** account + CLI

---

## Quick Local Start

1) Clone and install
```bash
git clone <your-repo>
cd xxl-bot-summarizer
uv sync
```

2) Create **.env** in the project root
```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...your_token...
OPENAI_API_KEY=sk-...your_openai_key...
GEMINI_API_KEY=AIza...your_gemini_key...
DATABASE_URL=postgresql://user:pass@host:5432/dbname
TZ=Europe/Kyiv

# Route each chat to a provider (comma-separated chat IDs).
# A chat's summarizer provider is decided by which set it appears in.
OPENAI_CHAT_IDS=-1001234567890,-1009876543210
GEMINI_CHAT_IDS=-1001111111111,-1002222222222

# Chats where the sarcastic PanBot auto-responder is active:
PANBOT_CHAT_IDS=-1001234567890

# Optional:
OPENAI_MODEL_NAME=gpt-4o-mini
GEMINI_MODEL_NAME=gemini-2.5-flash
PET_CONFIDENCE_THRESHOLD=0.6
# Where to write the rotating log file (defaults to /app/data/bot.log for Fly).
# Set to a writable local path when running outside the container:
LOG_FILE=./bot.log
```

> `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, and `GEMINI_API_KEY` are read at startup and the process exits if any is missing. `DATABASE_URL` is required for every database operation.

3) Run locally
```bash
uv run python -m src.main
```
> Run it as a module (`-m src.main`) — the code uses absolute `src.*` imports.

4) Test in a chat
- Send several messages and replies in a test group
- `/summary_now [0-9]` — today's summary at the given toxicity level (defaults to 9)
- `/chatid` — chat id and which services are configured for it
- `/enable_summaries` / `/disable_summaries` — toggle the automatic daily summary
- `/petfinder` — cats/dogs found among today's photos
- In a `PANBOT_CHAT_IDS` chat, mention `ботяндра` to get a sarcastic reply

> Get a chat id without `/chatid`:
> - Copy the link to any message in a private supergroup: `https://t.me/c/<internal_id>/<msg_id>` → your `chat_id` is `-100<internal_id>`.
> - Bot API: `curl -s "https://api.telegram.org/bot$TOKEN/getUpdates" | jq '.result[].message.chat'` (run `deleteWebhook` first if needed).

---

## Available Commands
- **/summary_now [0-9]** — Immediate summary with optional toxicity level (0 = friendly, 9 = maximum toxicity; default 9)
- **/chatid** — Show chat ID and the configured services (OpenAI / Gemini / PanBot)
- **/enable_summaries** — Enable the automatic daily summary for this chat
- **/disable_summaries** — Disable the automatic daily summary for this chat
- **/status_summaries** — Show the AI provider and whether daily summaries are enabled
- **/petfinder** — List today's cat/dog photos with ironic captions

---

## AI Provider Configuration

The summarizer supports both **OpenAI** and **Gemini**. A chat's provider is determined **solely** by which set it belongs to:

- **OPENAI_CHAT_IDS**: chat IDs that use OpenAI (GPT) models
- **GEMINI_CHAT_IDS**: chat IDs that use Gemini models

`ALLOWED_CHAT_IDS` is the union of the two and gates the summarizer and its commands — a chat in neither set is ignored. **PanBot** is gated separately by **PANBOT_CHAT_IDS**. Each chat should appear in exactly one of OpenAI/Gemini.

### Toxicity Levels
- **Level 0**: Friendly and positive tone
- **Level 1-3**: Light humor and mild criticism
- **Level 4-6**: Pronounced sarcasm and sharp criticism
- **Level 7-9**: Maximum toxicity and provocative style

If a safety filter blocks a request at a high level, the summarizer automatically retries down toward 0, and falls back to an ironic canned message if it is blocked all the way down.

---

## Development

```bash
uv sync                              # install deps (incl. dev group)
uv run python -m src.main            # run the bot locally
uv run pytest -q                     # run the test suite
uvx ruff@0.4.4 check src             # lint (matches CI + pre-commit)
pre-commit run --all-files           # ruff + hadolint (Dockerfile)
```

Tests set the required env vars and a temp `LOG_FILE` in `tests/conftest.py`; the database and LLM calls are mocked, so no live Postgres or API keys are needed to run them.

---

## Deploy to Fly.io (Machines)

### 1) Install & log in to flyctl
```bash
curl -L https://fly.io/install.sh | sh
fly auth login
```

### 2) Dockerfile
The project includes a **uv-based** Dockerfile:
```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
COPY . .
CMD ["uv", "run", "python", "-m", "src.main"]
```

### 3) Initialize the Fly app
```bash
fly launch --no-deploy
# pick a region close to Ukraine/Europe (e.g., fra/ams/waw)
```

### 4) Persistent volume (logs)
`fly.toml` mounts a volume at `/app/data`, where the rotating `bot.log` is written:
```toml
[mounts]
  source = "bot_data"
  destination = "/app/data"
```
Create it once:
```bash
fly volumes create bot_data --size 1
```
> Message history and settings live in **PostgreSQL** (`DATABASE_URL`), not on the volume — point `DATABASE_URL` at a managed/external Postgres.

### 5) Secrets & env vars
**Import from `.env`:**
```bash
fly secrets import < .env
# or set specific ones
fly secrets set TELEGRAM_BOT_TOKEN=... OPENAI_API_KEY=... GEMINI_API_KEY=... \
  DATABASE_URL=postgresql://... TZ=Europe/Kyiv \
  OPENAI_CHAT_IDS=-100... GEMINI_CHAT_IDS=-100... PANBOT_CHAT_IDS=-100...
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

CI does this automatically: `.github/workflows/linters.yml` runs ruff + hadolint on push, and `deploy.yml` runs `flyctl deploy` on `master` once linters pass.

## Update / Redeploy
- Code or Dockerfile changed: `fly deploy`
- Secrets/env only: `fly secrets import < .env` _(auto-restart)_
- Restart current release without rebuilding: `fly apps restart <app-name>`

---
## Test Checklist
- `/chatid` in the production chat returns a negative id (`-100…` for supergroups) and shows the correct services
- `/summary_now 0` produces a friendly summary; `/summary_now 9` a highly toxic one
- `/summary_now` produces a summary with clickable links to the first message and to the initiator
- A post arrives at **23:59 Europe/Kyiv** (if summaries are enabled): **#Підсумки_дня**
- `/petfinder` returns cat/dog links after photos have been posted today
- PanBot replies to `ботяндра`/`ботяндрік` in a `PANBOT_CHAT_IDS` chat and enforces the daily quota
- BotFather **Group Privacy = Disabled**
- Both OpenAI and Gemini chats work correctly

---
## Tips & Gotchas
- **Provider routing**: a chat's summarizer provider is decided purely by OpenAI/Gemini set membership; PanBot is separate.
- **Deferred pet detection**: `on_photo` only stores each photo's `file_id`; detection/captioning runs later when `/petfinder` is invoked.
- **Token limits**: tiktoken trims the message snippet to stay within the model budget.
- **Private supergroups**: message links look like `https://t.me/c/<internal_id>/<msg_id>` and work for chat members.
- **HTML escaping**: names/titles/summaries are escaped; all Telegram output uses `ParseMode.HTML`.
- **Event loop**: uses PTB's **JobQueue** to avoid event-loop conflicts.
- **Safety filters**: high toxicity levels that get blocked are retried at lower levels automatically.

---
## Troubleshooting
- `This event loop is already running` → don't call `run_polling()` inside `asyncio.run(...)`; use the structure in `src/main.py`
- `no running event loop` with schedulers → the project uses PTB JobQueue (see `src/tools/scheduler.py`)
- `DATABASE_URL must be set to use Postgres` → set `DATABASE_URL` in `.env`/secrets
- `Read-only file system: '/app'` when running locally → set `LOG_FILE` to a writable path
- `getUpdates conflict` → run `deleteWebhook` before `getUpdates` to fetch a chat id
- No midnight summary → check TZ, the JobQueue schedule, that summaries are enabled (`/enable_summaries`), and that Group Privacy is **disabled**
- API errors → check that the chat is configured in the correct `*_CHAT_IDS` variable
- Safety blocks → try lower toxicity levels or check API quotas/content policies
