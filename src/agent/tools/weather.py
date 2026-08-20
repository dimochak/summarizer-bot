"""Погода через Open-Meteo.

Обрано саме його, бо не потребує API-ключа й повертає готовий JSON. Через
веб-пошук те саме було б повільніше, менш надійно й потребувало б парсингу
сторінки.
"""
import httpx
from langchain_core.tools import tool

import src.tools.config as config

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 10.0

# Коди погоди WMO, які повертає Open-Meteo.
WEATHER_CODES = {
    0: "ясно",
    1: "переважно ясно",
    2: "мінлива хмарність",
    3: "похмуро",
    45: "туман",
    48: "паморозь",
    51: "мжичка",
    53: "мжичка",
    55: "сильна мжичка",
    61: "невеликий дощ",
    63: "дощ",
    65: "сильний дощ",
    66: "крижаний дощ",
    67: "сильний крижаний дощ",
    71: "невеликий сніг",
    73: "сніг",
    75: "сильний сніг",
    77: "снігова крупа",
    80: "короткочасний дощ",
    81: "злива",
    82: "сильна злива",
    85: "снігопад",
    86: "сильний снігопад",
    95: "гроза",
    96: "гроза з градом",
    99: "сильна гроза з градом",
}


@tool
async def get_weather(city: str) -> str:
    """Повертає поточну погоду у вказаному місті.

    Використовуй, коли питають про погоду, температуру, чи брати парасольку тощо.

    Args:
        city: назва міста, наприклад «Київ» або «Kyiv».
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            geo = await client.get(
                GEOCODE_URL,
                params={"name": city, "count": 1, "language": "uk", "format": "json"},
            )
            geo.raise_for_status()
            places = geo.json().get("results") or []
            if not places:
                return f"Місто «{city}» не знайдено."

            place = places[0]
            forecast = await client.get(
                FORECAST_URL,
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "timezone": "auto",
                },
            )
            forecast.raise_for_status()
            current = forecast.json()["current"]
    except Exception as e:
        config.log.exception("Weather lookup failed for %s: %s", city, e)
        return f"Не вдалося отримати погоду для «{city}»."

    name = place.get("name", city)
    country = place.get("country") or ""
    where = f"{name}, {country}".strip(", ")
    description = WEATHER_CODES.get(current.get("weather_code"), "невизначено")

    return (
        f"{where}: {current['temperature_2m']:.0f}°C "
        f"(відчувається як {current['apparent_temperature']:.0f}°C), "
        f"{description}, вітер {current['wind_speed_10m']:.0f} км/год."
    )
