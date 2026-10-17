# Voice messages and video notes → transcript + toxic reply (and snoring detection)

**Status:** implemented on `feature/voice-messages`. Real-sample eval (see Verification) is still pending before rollout.
**Created:** 2026-10-13
**Relation to `AGENT_MIGRATION_PLAN.md`:** a narrow first slice of Phase 4 (audio/video). It follows key decision #3 there: the transcript goes into `messages.text`, so voice messages show up in daily summaries and PanBot context with no changes to `summarizer` or `ChatContextHistory`. There's no `media_files` table and no telethon: voice notes and video notes are small enough for the Bot API and inline Gemini requests.

## Goal

When someone sends a voice message or a video note (кружечок) to a PanBot chat, the bot:

1. transcribes it;
2. replies with a randomly chosen toxic intro ("Невже так складно писати буквами?") followed by the transcript;
3. if the recording contains only snoring, replies with a random "прокидайся" phrase instead.

## Decisions

| Question | Decision |
|---|---|
| Which chats | `VOICE_CHAT_IDS`, same parsing as `AGENT_CHAT_IDS`; empty by default, so the first deploy changes nothing. Originally `PANBOT_CHAT_IDS`; changed after review so it can be rolled out on a test chat first and switched off without a code change (`VOICE_DAILY_LIMIT=0` is not an off switch: the first voice message of the day still gets the limit warning) |
| Forwarded voice messages | Mock the person who forwarded it, with separate phrase lists (`forwarded` in the templates) |
| Video notes | Supported in v1, same pipeline; Gemini takes `video/mp4` directly |
| Quota | 5 per person per chat per day (`VOICE_DAILY_LIMIT`). The first one over the limit gets one warning; later ones are ignored silently |
| Gemini free tier / privacy | Go ahead regardless |
| Bot creator (`CREATOR_USER_ID`) | Gets respectful phrases instead of toxic ones. This matches PanBot's existing rule for the creator (`system.j2`). Added during implementation, easy to drop |

## Why Gemini, not Whisper

| Option | Transcribes UA/RU/surzhyk | Detects snoring | Accepts Telegram OGG/Opus as is | Cost / min |
|---|---|---|---|---|
| **Gemini Flash (multimodal)** ✅ | yes | **yes**, non-speech sound understanding is a documented capability | yes (`audio/ogg`, `audio/opus`, `video/mp4`) | ~$0.002 (2.5-flash), ~$0.0006 (flash-lite) |
| OpenAI `gpt-transcribe` / `whisper-1` | yes | **no**, ASR only | **no**: OGG isn't in the current list of accepted formats, so it needs ffmpeg in the image | ~$0.003–0.006 |
| Local AudioSet classifier (YAMNet/PANNs) + any ASR | n/a | yes, AudioSet has an explicit "Snoring" class | needs ffmpeg + numpy + tflite in a 512 MB VM | CPU only |

Whisper-style ASR has no notion of "snoring". On non-speech input it either returns an empty string or hallucinates text (the well-known YouTube-outro phrases on silence). Gemini returns the classification and the transcript in one structured call. If the real-sample eval shows Gemini can't detect snoring reliably, the fallback is a local YAMNet gate, at the cost of a heavier image.

Audio costs 32 tokens per second (1 min = 1,920 tokens). The inline request limit is 20 MB including base64, so files above 14 MB are treated as too long. Real voice notes never get close.

## As built

### Flow

```
voice / video-note update
 └─ on_voice                               (src/tools/handlers.py, thin)
     ├─ chat ∉ VOICE_CHAT_IDS → return
     ├─ ensure_chat_record
     └─ schedule_voice(context, msg, is_creator)        (background task; PTB not blocked)
          └─ handle_voice                                (src/voice/reply.py)
               ├─ duration > VOICE_MAX_SECONDS or file > 14 MB → too_long.j2   # no download, no LLM, no quota
               ├─ claim_voice_slot → over limit → limit.j2 once, then silence
               ├─ "typing…" + download in memory
               ├─ analyze_voice → {kind, transcript}                           # 1 Gemini call
               │    └─ failure → release_voice_slot, failed.j2
               ├─ store the voice message: "🎤 <transcript>" | "[🎤 голосове: хропіння]" | "[🎤 голосове без слів]"
               ├─ reply: <kind>.j2 intro (+ transcript.j2 for speech), parse_mode=HTML
               └─ store the bot reply: intro only (the transcript is already stored once)
```

### Files

| File | What |
|---|---|
| `src/voice/analyze.py` | `VoiceAnalysis` schema, `build_message`, `analyze_voice`. Trusts the classification over the text: a non-speech kind drops any transcript, and speech with an empty transcript becomes `other` |
| `src/voice/reply.py` | `handle_voice`, `schedule_voice`, history storage, truncation to 3,500 chars |
| `src/voice/render.py` | Jinja2 environment (autoescape, `StrictUndefined`) and the `uk_duration` filter ("2 хвилини 5 секунд") |
| `src/voice/templates/analyze.j2` | Prompt for Gemini |
| `src/voice/templates/{speech,snoring,noise,too_long,limit,failed}.j2` | Toxic phrase lists, picked with Jinja's `random` filter. Branches: creator → forwarded → video note → default |
| `src/voice/templates/transcript.j2` | `🎙 <b>name</b>:` + `<blockquote expandable>transcript</blockquote>` |
| `src/core/llm.py` | Purpose `voice`, forced to Gemini; `VOICE_GEMINI_MODEL_NAME` override |
| `src/tools/db.py` | `voice_limits` table, `claim_voice_slot` / `release_voice_slot`, retention cleanup |
| `src/main.py` | `VOICE_FILTER = VOICE | VIDEO_NOTE`, explicitly excluded from the catch-all `TEXT_FILTER` |
| `src/voice/eval.py` | Manual eval against real Gemini on a folder of recordings |

### Config

```dotenv
VOICE_CHAT_IDS=             # empty — feature off
VOICE_GEMINI_MODEL_NAME=    # empty — GEMINI_MODEL_NAME
VOICE_MAX_SECONDS=300
VOICE_DAILY_LIMIT=5
```

## Verification

Done:

- `tests/test_voice.py`, using fakes for Telegram, the DB and the LLM. It covers:
  - template rendering in every combination;
  - HTML escaping of names and transcripts;
  - routing per kind;
  - limit and too-long paths skipping download and the model;
  - slot release on failure;
  - the transcript stored once;
  - forwarded and creator variants;
  - handler filters built from real PTB `Update` objects.

  One test runs our content block through `langchain_google_genai`'s own converter, to confirm it really becomes inline audio or video data.
- The `voice_limits` SQL ran against a real Postgres 16 (integration tests).
- Full suite and the CI ruff check (`0.16.3`, `E4,E7,E9,F`) pass.

Pending, and needed before relying on snoring detection:

- `uv run python -m src.voice.eval <dir>` on ~20 real recordings:
  - real chat voice notes in UA, RU and surzhyk, some with background noise;
  - real snoring (freesound.org has CC-licensed recordings; convert with `ffmpeg -c:a libopus`);
  - speech with snoring in the background;
  - silence, music and street noise;
  - a couple of video notes.
- Name the files `speech_*`, `snoring_*` and `other_*` so the script counts matches. Pass bar: zero speech recordings classified as snoring.
- Run it with the default model and with a flash-lite model. If flash-lite passes, it's about 3× cheaper.

## Out of scope / follow-ups

- **Spoken triggers.** "ботяндра, …" said in a voice message doesn't route into the normal PanBot reply yet. That needs `get_panbot_response` to accept text instead of reading `message.text`.
- **Background-snoring jab.** A `background_snoring` flag for speech with snoring in the background.
- **Image documents never reach `on_photo` (separate fix).** The catch-all `on_message` excludes only `PHOTO`, and handlers in one group are first-match. So image documents never reach `on_photo`, its document branch (`handlers.py`, `elif msg.document …`) is dead code, and images sent as files get no bayan detection. It's the same mechanism that used to swallow voice messages.
