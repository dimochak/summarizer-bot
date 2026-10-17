"""Гіфка на початок і кінець повітряної тривоги.

Джерело стану задає ALERTS_SOURCE:
    jaam         — jaam.net.ua, без токена; сам бере дані з офіційного
                   ukrainealarm.com, тож це найближче до першоджерела;
    alerts_in_ua — alerts.in.ua, потрібен токен.

Джоб опитує джерело раз на ALERTS_POLL_SECONDS і постить лише на переході
«немає тривоги -> тривога» чи навпаки. Сам стан зберігається в БД.

Це розвага для чату, а не система оповіщення: дані сервісу можуть
запізнюватись, а бот — лежати. Покладатись на нього не можна.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import time

import httpx
from telegram.ext import Application, ContextTypes

import src.tools.config as config
from src.reactions.react import ANIMATION_EXTENSIONS, list_media, pick_media
from src.tools.db import db_call, record_air_alert_state

JAAM_URL = "https://jaam.net.ua/alerts_statuses_v2.json"
ALERTS_IN_UA_URL = "https://api.alerts.in.ua/v1/iot/active_air_raid_alerts/{uid}.json"
TIMEOUT = 10.0
# Обидва сервіси безкоштовні; хай бачать, хто до них ходить.
USER_AGENT = "xxl-summarizer-bot (+https://github.com/dimochak/summarizer-bot)"

# Областей в Україні 24 + Крим; відповідь із меншою кількістю регіонів —
# збій на боці JAAM, а не «тривоги ніде немає».
JAAM_MIN_STATES = 20

# Після 429 мовчки пропускаємо опитування стільки секунд: долбити далі —
# прямий шлях до бану IP.
RATE_LIMIT_BACKOFF = 60

# alerts.in.ua: "A" — тривога в усьому регіоні, "P" — частково (в окремих
# районах чи громадах), "N" — тривоги немає.
ALERTS_IN_UA_ACTIVE = {"A", "P"}
ALERTS_IN_UA_KNOWN = ALERTS_IN_UA_ACTIVE | {"N"}

# Теки з гіфками поруч із теками реакцій.
ALERT_DIR = "air_alert"
ALL_CLEAR_DIR = "all_clear"


@dataclass(frozen=True)
class AlertStatus:
    active: bool
    # Тривога лише в частині регіону. Рахуємо її як тривогу; поле — для лога.
    partial: bool = False
    # Коли джерело зафіксувало останню зміну. Є лише в JAAM; потрібне, щоб
    # бачити в лозі, наскільки бот відстає від джерела.
    changed_at: datetime | None = None


# Last-Modified останньої відповіді alerts.in.ua. Сервіс просить кешувати через
# If-Modified-Since: на 304 він не віддає тіло, і це дешевше для обох сторін.
_cache: dict = {
    "last_modified": None,
    "status": None,
    "backoff_until": 0.0,
    "jaam_missing_logged": False,
}

# file_id уже завантажених у Telegram гіфок. Повторна відправка за file_id
# миттєва, а завантаження файлу на 1-2 МБ — це секунди саме тоді, коли
# швидкість важлива найбільше. Живе до рестарту.
_file_ids: dict[Path, str] = {}


def region_key() -> str:
    if config.ALERTS_SOURCE == "jaam":
        return f"jaam:{config.ALERTS_JAAM_REGION}"
    return f"alerts_in_ua:{config.ALERTS_REGION_UID}"


async def _get(client: httpx.AsyncClient, url: str, headers: dict) -> httpx.Response | None:
    """GET з обробкою збоїв. None — відповіді немає або вона непридатна."""
    if time() < _cache["backoff_until"]:
        return None
    try:
        response = await client.get(
            url, headers={"User-Agent": USER_AGENT, **headers}, timeout=TIMEOUT
        )
    except httpx.HTTPError as e:
        config.log.warning(f"Air raid: запит не вдався: {e!r}")
        return None

    if response.status_code == 429:
        _cache["backoff_until"] = time() + RATE_LIMIT_BACKOFF
        config.log.warning(f"Air raid: 429, пауза {RATE_LIMIT_BACKOFF}с")
        return None
    if response.status_code not in (200, 304):
        config.log.warning(f"Air raid: HTTP {response.status_code}: {response.text[:200]}")
        return None
    return response


def _json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        config.log.warning(f"Air raid: не JSON: {response.text[:200]}")
        return None


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def fetch_jaam(client: httpx.AsyncClient) -> AlertStatus | None:
    response = await _get(client, JAAM_URL, {})
    if response is None or response.status_code != 200:
        return None
    data = _json(response)
    states = data.get("states") if isinstance(data, dict) else None
    if not isinstance(states, dict):
        config.log.warning(f"Air raid: JAAM без states: {response.text[:200]}")
        return None

    # Обрізана чи порожня відповідь не має виглядати як «тривоги немає» —
    # інакше бот оголосив би відбій посеред тривоги.
    if len(states) < JAAM_MIN_STATES:
        config.log.warning(f"Air raid: JAAM віддав лише {len(states)} регіонів, пропускаємо")
        return None

    entry = states.get(config.ALERTS_JAAM_REGION)
    if entry is None:
        # Міста-громади («м. Київ», «м. Харків…») зʼявляються у відповіді
        # лише під час тривоги, тож відсутній ключ означає «тривоги немає».
        # Кажемо про це раз: якщо в назві описка, ключ не зʼявиться ніколи.
        if not _cache["jaam_missing_logged"]:
            _cache["jaam_missing_logged"] = True
            config.log.info(
                f"Air raid: ключа {config.ALERTS_JAAM_REGION!r} у JAAM зараз немає — "
                f"вважаємо, що тривоги немає. Є: {', '.join(states)}"
            )
        return AlertStatus(active=False)
    _cache["jaam_missing_logged"] = False

    if not isinstance(entry, dict) or not isinstance(entry.get("alertnow"), bool):
        config.log.warning(f"Air raid: незрозумілий запис JAAM для {config.ALERTS_JAAM_REGION!r}: {entry!r}")
        return None

    return AlertStatus(
        active=entry["alertnow"],
        partial=bool(entry.get("district")),
        changed_at=_parse_time(entry.get("changes")),
    )


async def fetch_alerts_in_ua(client: httpx.AsyncClient) -> AlertStatus | None:
    headers = {"Authorization": f"Bearer {config.ALERTS_API_TOKEN}"}
    if _cache["last_modified"]:
        headers["If-Modified-Since"] = _cache["last_modified"]

    response = await _get(
        client, ALERTS_IN_UA_URL.format(uid=config.ALERTS_REGION_UID), headers
    )
    if response is None:
        return None
    if response.status_code == 304:
        status = _cache["status"]
    else:
        status = _json(response)
        if status not in ALERTS_IN_UA_KNOWN:
            config.log.warning(f"Air raid: невідомий статус {status!r}")
            return None
        _cache["status"] = status
        _cache["last_modified"] = response.headers.get("Last-Modified")

    if status is None:
        return None
    return AlertStatus(active=status in ALERTS_IN_UA_ACTIVE, partial=status == "P")


SOURCES = {"jaam": fetch_jaam, "alerts_in_ua": fetch_alerts_in_ua}


async def fetch_status(client: httpx.AsyncClient) -> AlertStatus | None:
    """Поточний стан регіону або None, якщо дізнатись не вдалось.

    None означає «не знаємо», а не «тривоги немає»: на збої мережі чи 429
    стан не змінюємо, інакше бот оголосив би відбій посеред тривоги.
    """
    return await SOURCES[config.ALERTS_SOURCE](client)


def alert_media_dir(active: bool) -> Path:
    return Path(config.REACTIONS_DIR) / (ALERT_DIR if active else ALL_CLEAR_DIR)


def alert_caption(active: bool) -> str:
    if active:
        return f"🚨 Повітряна тривога — {config.ALERTS_REGION_NAME}"
    return f"✅ Відбій тривоги — {config.ALERTS_REGION_NAME}"


def _sent_file_id(message) -> str | None:
    # Telegram може повернути mp4 як animation, video чи document.
    for attr in ("animation", "video", "document"):
        media = getattr(message, attr, None)
        if media is not None:
            return media.file_id
    photos = getattr(message, "photo", None)
    return photos[-1].file_id if photos else None


async def _send_media(bot, chat_id: int, media: Path, caption: str):
    send = bot.send_animation if media.suffix.lower() in ANIMATION_EXTENSIONS else bot.send_photo
    field = "animation" if send == bot.send_animation else "photo"

    cached = _file_ids.get(media)
    if cached:
        try:
            return await send(chat_id=chat_id, caption=caption, **{field: cached})
        except Exception as e:
            # file_id міг протухнути — тоді просто завантажуємо файл заново.
            config.log.warning(f"Air raid: file_id для {media.name} не спрацював: {e}")
            _file_ids.pop(media, None)

    with media.open("rb") as f:
        sent = await send(chat_id=chat_id, caption=caption, **{field: f})
    file_id = _sent_file_id(sent)
    if file_id:
        _file_ids[media] = file_id
    return sent


async def announce(bot, chat_id: int, active: bool) -> None:
    """Гіфка з коротким підписом; якщо гіфок немає — просто текст."""
    caption = alert_caption(active)
    media = pick_media(alert_media_dir(active))
    if media is None:
        await bot.send_message(chat_id=chat_id, text=caption)
        return
    await _send_media(bot, chat_id, media, caption)


async def _announce_safely(bot, chat_id: int, active: bool) -> None:
    try:
        await announce(bot, chat_id, active)
    except Exception as e:
        config.log.exception(f"Air raid: не вдалось надіслати в {chat_id}: {e}")


async def check_air_raid(bot, client: httpx.AsyncClient) -> None:
    status = await fetch_status(client)
    if status is None:
        return

    active = status.active
    change = await db_call(record_air_alert_state, region_key(), active, int(time()))
    if change != "changed":
        return

    # Стан уже збережено, тож збій відправки не призведе до повторного поста
    # на кожному наступному опитуванні.
    # У всі чати одночасно: повільний чат не має затримувати решту.
    await asyncio.gather(
        *(_announce_safely(bot, chat_id, active) for chat_id in config.ALERTS_CHAT_IDS)
    )

    what = "тривога" if active else "відбій"
    if status.partial:
        what += " (частина регіону)"
    lag = ""
    if status.changed_at is not None:
        lag = f", відставання {time() - status.changed_at.timestamp():.0f}с від джерела"
    config.log.info(f"Air raid: {what}, {config.ALERTS_SOURCE}{lag}")


async def air_raid_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    client = context.bot_data.get("alerts_http")
    if client is None:
        client = context.bot_data["alerts_http"] = httpx.AsyncClient()
    try:
        await check_air_raid(context.bot, client)
    except Exception as e:
        # Джоб повторюється, тож виняток звідси лише засмічував би лог трейсбеками
        # PTB; ловимо й пишемо самі.
        config.log.exception(f"Air raid job failed: {e}")


def _disabled_reason() -> str | None:
    if not config.ALERTS_CHAT_IDS:
        return "немає ALERTS_CHAT_IDS"
    if config.ALERTS_SOURCE not in SOURCES:
        return f"невідоме ALERTS_SOURCE={config.ALERTS_SOURCE!r} (є: {', '.join(SOURCES)})"
    if config.ALERTS_SOURCE == "alerts_in_ua" and not config.ALERTS_API_TOKEN:
        return "alerts_in_ua без ALERTS_API_TOKEN"
    return None


def schedule_air_raid(app: Application) -> None:
    reason = _disabled_reason()
    if reason:
        config.log.info(f"Air raid: вимкнено ({reason})")
        return
    app.job_queue.run_repeating(
        air_raid_job, interval=config.ALERTS_POLL_SECONDS, first=5, name="air_raid"
    )
    for active in (True, False):
        directory = alert_media_dir(active)
        config.log.info(f"Air raid: гіфок у {directory}: {len(list_media(directory))}")
    config.log.info(
        f"Air raid: {config.ALERTS_SOURCE}, регіон {region_key()}, "
        f"опитування кожні {config.ALERTS_POLL_SECONDS}с, чати {config.ALERTS_CHAT_IDS}"
    )


async def close_air_raid_client(app: Application) -> None:
    client = app.bot_data.pop("alerts_http", None)
    if client is not None:
        await client.aclose()
