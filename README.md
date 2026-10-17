# PanBot — a Telegram bot for group chats

A sarcastic bot for Ukrainian group chats: daily summaries, in-chat replies,
duplicate-image detection and participant profiles. Runs on OpenAI or Gemini —
the provider is configured per chat.

---

## Features

**Daily summaries.** At 23:59 (Europe/Kyiv) it posts **#Підсумки_дня**: the topics
discussed during the day, each linking to the topic's first message and to its
initiator. Tone is controlled by a toxicity level of 0–9. If the model's safety
filter blocks the request, the bot automatically retries at a lower level, down to 0.

**In-chat replies (PanBot).** Responds to direct address (`ботяндра`, `ботяндрік`,
`пан бот`), to replies to its own messages, and to requests for a comment
(`прокоментуй`, `що думаєш`, `що скажеш`, `твій коментар`). Whether to actually reply
is decided by a separate LLM classifier. Context is assembled from the reply thread
rather than the whole chat. Daily limit: 10 requests per user.

**On-demand summaries.** In chat: "підсумуй останні 100 повідомлень",
"що було за 5 годин", "що відбулось за 30 хв".

**Duplicate image detection.** If a picture was already posted in this chat, the bot
points that out with a link to the original.

**Air raid alerts.** The bot posts a GIF when an air raid alert starts in the configured
region and another one when it ends. Data comes from [JAAM](https://jaam.net.ua/api)
(no token needed; it mirrors the official ukrainealarm.com feed) or from alerts.in.ua.
GIFs live outside the repo, see `assets/reactions/README.md`. This is chat fun, not a
warning system: use the official alert app for that.

**Voice messages and video notes.** In chats listed in `VOICE_CHAT_IDS` (none by
default), the bot transcribes voice messages and round video notes with Gemini and
replies with a toxic remark plus the transcript ("Невже так складно писати буквами?").
A snoring-only recording gets a "прокидайся" instead, and forwarding someone else's
voice message earns its own jab. Limits: 5 transcriptions per person per day and
5 minutes per message. The transcript is stored as the message text, so voice messages
show up in daily summaries and in PanBot's context. Reply phrases live in
`src/voice/templates/*.j2`, so adding one is a one-file edit.

**Participant profiles (traits).** Tone, topics, verbosity and language preferences
feed into the bot's replies. Refreshed automatically every 30 days.

**Custom persona.** The bot's creator can rewrite its persona straight from the chat:
`ботяндра, твоя нова роль <description>`.

---

## Stack

- **Python 3.11+**, **uv** for dependency management
- **python-telegram-bot 21** in long-polling mode, `JobQueue` for scheduled jobs
- **LangChain** — a single client factory (`src/core/llm.py`) for both OpenAI and Gemini
- **PostgreSQL** via psycopg 3 with a connection pool
- **Fly.io** for deployment, **GitHub Actions** for linting, tests and auto-deploy

---

## Commands

| Command | Description |
|---|---|
| `/summary_now [0-9]` | Summary for today; the argument sets the toxicity level (defaults to 9) |
| `/chatid` | Shows the chat_id and which services are enabled for this chat |
| `/enable_summaries` | Enables automatic daily summaries |
| `/disable_summaries` | Disables them |
| `/status_summaries` | Shows the provider and the state of daily summaries |

---

## Configuration

### Required

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

### Which chats to serve

Comma-separated chat IDs. A chat may appear in several lists at once.

```dotenv
# Summary provider — decides whose model serves the chat
OPENAI_CHAT_IDS=-1001234567890
GEMINI_CHAT_IDS=-1001111111111

# Where the bot replies in chat
PANBOT_CHAT_IDS=-1001234567890
```

A chat may be listed in `PANBOT_CHAT_IDS` **only** — the bot will reply there but
will not produce daily summaries.

### Optional

```dotenv
TZ=Europe/Kyiv

# Models
OPENAI_MODEL_NAME=gpt-5.2
GEMINI_MODEL_NAME=gemini-2.5-flash
# A cheaper model used solely for the "should I reply?" decision
REPLY_DECISION_OPENAI_MODEL_NAME=
REPLY_DECISION_GEMINI_MODEL_NAME=
TRAITS_LLM_MODEL=gpt-5

# Air raid alerts (off until ALERTS_CHAT_IDS is set)
ALERTS_CHAT_IDS=            # comma-separated chat IDs
ALERTS_SOURCE=jaam          # jaam | alerts_in_ua
ALERTS_JAAM_REGION=м. Київ  # JAAM key: "м. Київ" (city), "Київська область"; not "Київ" (always empty)
ALERTS_API_TOKEN=           # alerts_in_ua only
ALERTS_REGION_UID=31        # alerts_in_ua only: 31 = Kyiv city, 14 = Kyiv oblast
ALERTS_REGION_NAME=м. Київ  # shown in the message
ALERTS_POLL_SECONDS=        # default and minimum: 10 for jaam, 8 for alerts_in_ua

# Voice messages and video notes (always Gemini, whatever the chat's provider)
VOICE_CHAT_IDS=             # where to transcribe; empty — feature off
VOICE_GEMINI_MODEL_NAME=    # empty — use GEMINI_MODEL_NAME
VOICE_MAX_SECONDS=300       # longer ones are not transcribed
VOICE_DAILY_LIMIT=5         # transcriptions per person per chat per day

# Database
DB_RETENTION_DAYS=30        # old data is purged daily at 04:00
DB_POOL_MAX_SIZE=10

# Participant profiles, refreshed daily at 05:00
TRAITS_REFRESH_DAYS=30      # how often each individual profile is refreshed
TRAITS_REFRESH_BATCH=50     # max profiles per run
TRAITS_REFRESH_CONCURRENCY=3

# Diagnostics
LOG_FILENAME=bot.log
LANGCHAIN_DEBUG=false
```

---

## Running locally

1. Create a bot via [@BotFather](https://t.me/botfather), disable Group Privacy
   (`/setprivacy` → Disable) so the bot can see all chat messages, and add it to a group.

2. Install dependencies and prepare a `.env` following the example above:

```bash
uv sync
```

3. Run it:

```bash
uv run python -m src.main
```

To find a `chat_id`, add the bot to the chat and call `/chatid`. Alternatively, copy a
link to any message in a private supergroup — `https://t.me/c/<id>/<msg>` — and the
`chat_id` is `-100<id>`.

---

## Tests

```bash
uv run pytest
```

Tests need neither a `.env` nor a live database: `tests/conftest.py` supplies default
environment values and blocks any attempt to open a real database connection or call
an LLM.

Integration tests run against a real Postgres and are skipped without one:

```bash
XXL_TEST_DATABASE_URL=postgresql://... uv run pytest -m integration
```

---

## Layout

```
src/
  core/llm.py          # single LLM client factory (provider + model per purpose)
  panbot/              # in-chat replies
    engine/            # response generation and the reply decision
    history/           # context assembly from the reply thread
    prompts/           # prompts as Jinja2 templates
    formatting.py      # coerces LLM output into HTML that Telegram accepts
  summarizer/          # daily summaries
  traits/              # participant profiles
  reactions/           # GIF replies to mentions of chosen subjects
  alerts/              # air raid alert GIFs
  tools/               # config, database, handlers, scheduler
```

---

## Deployment

Pushing to `master` triggers GitHub Actions: linting (ruff) and tests (pytest), and
only once both pass does `flyctl deploy` run. Other branches go through the checks
but are not deployed.

---

## Roadmap

The bot is gradually moving to a tool-calling agent architecture: searching the web
for current information, handling audio and video, and longer-term memory.
The full plan lives in [docs/AGENT_MIGRATION_PLAN.md](docs/AGENT_MIGRATION_PLAN.md).
