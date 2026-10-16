"""Гіфка на початок і кінець повітряної тривоги.

Стан регіону береться з alerts.in.ua (IoT-ендпоінт: одна літера на регіон).
Джоб опитує його раз на ALERTS_POLL_SECONDS і постить лише на переході
«немає тривоги -> тривога» чи навпаки. Сам стан зберігається в БД.

Це розвага для чату, а не система оповіщення: дані сервісу можуть
запізнюватись, а бот — лежати. Покладатись на нього не можна.
"""
from pathlib import Path
from time import time

import httpx
from telegram.ext import Application, ContextTypes

import src.tools.config as config
from src.reactions.react import ANIMATION_EXTENSIONS, list_media, pick_media
from src.tools.db import db_call, record_air_alert_state

API_URL = "https://api.alerts.in.ua/v1/iot/active_air_raid_alerts/{uid}.json"
TIMEOUT = 10.0

# "A" — тривога в усьому регіоні, "P" — частково (в окремих районах чи
# громадах), "N" — тривоги немає.
ACTIVE_STATUSES = {"A", "P"}
KNOWN_STATUSES = ACTIVE_STATUSES | {"N"}

# Теки з гіфками поруч із теками реакцій.
ALERT_DIR = "air_alert"
ALL_CLEAR_DIR = "all_clear"

# Last-Modified останньої відповіді. Сервіс просить кешувати через
# If-Modified-Since: на 304 він не віддає тіло, і це дешевше для обох сторін.
_cache: dict[str, str | None] = {"last_modified": None, "status": None}


async def fetch_status(client: httpx.AsyncClient) -> str | None:
    """Поточна літера статусу регіону або None, якщо дізнатись не вдалось.

    None означає «не знаємо», а не «тривоги немає»: на збої мережі чи 429
    стан не змінюємо, інакше бот оголосив би відбій посеред тривоги.
    """
    headers = {"Authorization": f"Bearer {config.ALERTS_API_TOKEN}"}
    if _cache["last_modified"]:
        headers["If-Modified-Since"] = _cache["last_modified"]

    try:
        response = await client.get(
            API_URL.format(uid=config.ALERTS_REGION_UID), headers=headers, timeout=TIMEOUT
        )
    except httpx.HTTPError as e:
        config.log.warning(f"Air raid: запит не вдався: {e!r}")
        return None

    if response.status_code == 304:
        return _cache["status"]
    if response.status_code != 200:
        config.log.warning(f"Air raid: HTTP {response.status_code}: {response.text[:200]}")
        return None

    try:
        status = response.json()
    except ValueError:
        config.log.warning(f"Air raid: не JSON: {response.text[:200]}")
        return None
    if status not in KNOWN_STATUSES:
        config.log.warning(f"Air raid: невідомий статус {status!r}")
        return None

    _cache["status"] = status
    _cache["last_modified"] = response.headers.get("Last-Modified")
    return status


def alert_media_dir(active: bool) -> Path:
    return Path(config.REACTIONS_DIR) / (ALERT_DIR if active else ALL_CLEAR_DIR)


def alert_caption(active: bool) -> str:
    if active:
        return f"🚨 Повітряна тривога — {config.ALERTS_REGION_NAME}"
    return f"✅ Відбій тривоги — {config.ALERTS_REGION_NAME}"


async def announce(bot, chat_id: int, active: bool) -> None:
    """Гіфка з коротким підписом; якщо гіфок немає — просто текст."""
    caption = alert_caption(active)
    media = pick_media(alert_media_dir(active))
    if media is None:
        await bot.send_message(chat_id=chat_id, text=caption)
        return
    with media.open("rb") as f:
        if media.suffix.lower() in ANIMATION_EXTENSIONS:
            await bot.send_animation(chat_id=chat_id, animation=f, caption=caption)
        else:
            await bot.send_photo(chat_id=chat_id, photo=f, caption=caption)


async def check_air_raid(bot, client: httpx.AsyncClient) -> None:
    status = await fetch_status(client)
    if status is None:
        return

    active = status in ACTIVE_STATUSES
    change = await db_call(
        record_air_alert_state, config.ALERTS_REGION_UID, active, int(time())
    )
    if change != "changed":
        return

    # Стан уже збережено, тож збій відправки не призведе до повторного поста
    # на кожному наступному опитуванні.
    config.log.info(f"Air raid: {'тривога' if active else 'відбій'} (статус {status})")
    for chat_id in config.ALERTS_CHAT_IDS:
        try:
            await announce(bot, chat_id, active)
        except Exception as e:
            config.log.exception(f"Air raid: не вдалось надіслати в {chat_id}: {e}")


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


def schedule_air_raid(app: Application) -> None:
    if not config.ALERTS_API_TOKEN or not config.ALERTS_CHAT_IDS:
        config.log.info("Air raid: вимкнено (немає ALERTS_API_TOKEN або ALERTS_CHAT_IDS)")
        return
    app.job_queue.run_repeating(
        air_raid_job, interval=config.ALERTS_POLL_SECONDS, first=5, name="air_raid"
    )
    for active in (True, False):
        directory = alert_media_dir(active)
        config.log.info(f"Air raid: гіфок у {directory}: {len(list_media(directory))}")
    config.log.info(
        f"Air raid: опитування кожні {config.ALERTS_POLL_SECONDS}с, "
        f"регіон {config.ALERTS_REGION_UID}, чати {config.ALERTS_CHAT_IDS}"
    )



async def close_air_raid_client(app: Application) -> None:
    client = app.bot_data.pop("alerts_http", None)
    if client is not None:
        await client.aclose()
