# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Telegram group-chat bot (Ukrainian-language) that runs four features on incoming messages/photos/audio:

1. **Summarizer** — at 23:59 Europe/Kyiv (and on-demand via `/summary_now`) it clusters the day's messages into topics with an LLM and posts a `#Підсумки_дня` summary, with links to the first message and initiator of each topic. Supports a toxicity level 0–9.
2. **PanBot** — a sarcastic auto-responder that replies when a message contains a trigger word (`ботяндра`/`ботяндрік`) or is a reply to a bot message, subject to a per-user daily quota.
3. **PetFinder** — `/petfinder` scans the day's photos, uses a vision model to detect cats/dogs, and posts links with ironic captions.
4. **Transcriber** — transcribes voice messages and round video notes with Gemini, replies with the text, and stores it as the message (attributed to the original speaker) so it feeds the daily summary. Gated at startup by `TRANSCRIPTION_ENABLED`.

The bot runs in **long-polling** mode (`app.run_polling`), not webhooks.

## Commands

```bash
uv sync --python 3.13                 # install deps (pin 3.13; 3.14 lacks wheels for tiktoken/pydantic-core)
uv run python -m src.main             # run the bot locally (needs .env)
uv run pytest -q                      # run tests (DB + LLM are mocked; no live services needed)
uv run pytest tests/test_panbot.py::test_daily_limit_enforced   # single test
uvx ruff@0.4.4 check src              # lint (ruff is a pre-commit hook, not a project dep)
pre-commit run --all-files            # ruff + hadolint (Dockerfile)
```

Run the bot as a module (`python -m src.main`), not `python src/main.py` — the code uses absolute `src.*` imports. `tests/conftest.py` sets the required env vars (including `LOG_FILE`) before any `src` import, and `[tool.pytest.ini_options]` in `pyproject.toml` puts the repo root on `sys.path`.

## Required environment (.env)

`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `OPENAI_API_KEY` are **required at import time** (`config.py` reads them with `os.environ[...]`, so a missing one crashes on startup). `DATABASE_URL` (Postgres) is required for any DB operation. Chat routing is controlled by comma-separated ID env vars: `OPENAI_CHAT_IDS`, `GEMINI_CHAT_IDS`, `PANBOT_CHAT_IDS`. Optional: `TZ` (default `Europe/Kyiv`), `OPENAI_MODEL_NAME` (default `gpt-4o-mini`), `GEMINI_MODEL_NAME` (default `gemini-2.5-flash`), `PET_CONFIDENCE_THRESHOLD` (default `0.6`), `TRANSCRIPTION_ENABLED` (default `true`; parsed via `config.env_flag`, set `false`/`0`/`no`/`off` to skip registering the voice/video-note handler at startup), `LOG_FILE` (default `/app/data/bot.log` — the Fly volume path; override it when running outside the container or the loguru file sink fails with a read-only-`/app` error on import).

## Architecture

Entry point is [src/main.py](src/main.py): it calls `init_db()`, registers handlers on a PTB `Application`, schedules the daily job, and starts polling.

- **[src/tools/config.py](src/tools/config.py)** — the central config/singleton module. Every other module does `import src.tools.config as config`. It loads `.env`, parses the chat-ID sets, sets up loguru (file `/app/data/bot.log` + stderr, intercepting stdlib `logging`), and exposes `config.log`, `config.KYIV`, and the `*_CHAT_IDS` sets. **Side effects run on import.**
- **[src/tools/handlers.py](src/tools/handlers.py)** — all Telegram handler callbacks (`on_message`, `on_photo`, and the `cmd_*` command handlers). `on_message` persists every message and triggers PanBot; `on_photo` only stores photo `file_id`s for *deferred* detection (detection happens later in `/petfinder`, not on receipt).
- **[src/tools/db.py](src/tools/db.py)** — Postgres access via `psycopg` with `dict_row`. `SCHEMA` string holds all `CREATE TABLE`s (`messages`, `chats`, `panbot_limits`, `pet_photos`, `photo_messages`); `init_db()` executes them and enables summaries for all allowed chats. There is no ORM and no migration framework — schema changes go in the `SCHEMA` string (all statements are `IF NOT EXISTS`). Every function opens/closes its own connection.
- **[src/tools/scheduler.py](src/tools/scheduler.py)** — uses PTB's `JobQueue` (`run_daily`) for the midnight summary, deliberately **not** APScheduler/asyncio directly, to avoid event-loop conflicts. Only chats that are both `enabled` in the DB *and* in `ALLOWED_CHAT_IDS` get summarized.
- **[src/summarizer/summarizer.py](src/summarizer/summarizer.py)** — `summarize_day()` is the core. It reads messages for the window, builds a token-budgeted snippet with `tiktoken`, and calls OpenAI or Gemini depending on which set the chat is in. Both providers are asked for JSON. Key behavior: it **retries from the requested toxicity level down to 0** on safety-filter blocks, and falls back to an ironic canned message if blocked all the way down. Output is HTML (escaped) with `message_link`/`user_link` anchors.
- **[src/panbot/bot.py](src/panbot/bot.py)** — `PanBot` class; `should_reply()` decides whether to respond, `build_conversation_prompt()` pulls ~12h of chat context from the DB, `process_reply()` enforces the daily quota (`panbot_limits` table) and raises `SarcasmLimitExceeded`.
- **[src/petfinder/pets.py](src/petfinder/pets.py)** — `detect_and_caption_by_file_id()` downloads a Telegram photo and does joint pet-detection + caption in one OpenAI vision call returning JSON `{species, confidence, caption}`.
- **[src/transcriber/transcribe.py](src/transcriber/transcribe.py)** — `transcribe_by_file_id()` downloads a Telegram audio/video file and sends it inline to Gemini (bytes + mime_type) for a verbatim transcript. The `on_voice_video` handler stores the transcript via `add_message` (under the original speaker/message_id, so summaries link/attribute it) and replies with it. Registered in `main.py` only when `TRANSCRIPTION_ENABLED`; voice/video-note are excluded from the generic `on_message` filter so this handler wins.

### Provider routing
A chat's LLM provider is determined **only** by set membership: `OPENAI_CHAT_IDS` vs `GEMINI_CHAT_IDS`. `ALLOWED_CHAT_IDS` is their union and gates the summarizer/commands. A chat in neither set is ignored by `on_message` and rejected by the commands. PanBot is gated separately by `PANBOT_CHAT_IDS`.

## Conventions

- **User-facing strings are Ukrainian.** LLM prompts (the toxicity-level system prompts in `summarizer.py`, pet prompts in `pets.py`) are also Ukrainian — preserve tone/language when editing them.
- **All Telegram output is `ParseMode.HTML`** and must be escaped via `html.escape` (see `user_link`/`utils.py`) to avoid broken markup.
- **Timestamps** are stored as integer UTC epoch (`utc_ts`); display/day-boundary math converts to `config.KYIV` (see `local_midnight_bounds`).
- Bot's own messages are stored with `user_id = config.BOT_USER_ID` (`-1`) so PanBot can detect replies to itself.

## Deployment

Deployed to Fly.io (app `xxl-bot-summarizer`, region `waw`). CI in [.github/workflows](.github/workflows): `linters.yml` runs ruff + hadolint on push; `deploy.yml` runs `flyctl deploy` only after linters succeed on `master`. A persistent volume `bot_data` is mounted at `/app/data` (holds the log file). The Dockerfile builds on `python3.13` via `uv sync --frozen --no-dev`.

## Known gotchas

- **Local runs need a writable `LOG_FILE`.** `config.py`'s loguru sink defaults to `/app/data/bot.log`; outside the Fly container this dir is read-only and importing `src.*` fails. Set `LOG_FILE` (the test suite does this in `conftest.py`).
- **Pin Python to 3.13.** `uv` may default to 3.14, which has no prebuilt wheels for `tiktoken`/`pydantic-core` and forces a from-source build (needs a Rust toolchain). Use `uv sync --python 3.13` to match the Dockerfile.
