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


MAX_FORECAST_DAYS = 7


@tool
async def get_weather(city: str, forecast_days: int = 0) -> str:
    """Повертає погоду у вказаному місті: поточну і, за потреби, прогноз.

    Використовуй, коли питають про погоду, температуру, чи брати парасольку,
    чи буде дощ тощо.

    Args:
        city: назва міста, наприклад «Київ» або «Kyiv».
        forecast_days: скільки днів прогнозу потрібно понад сьогодні.
            0 — лише поточна погода (питають «зараз», «сьогодні»).
            1 — плюс завтра. 3-5 — коли питають про вихідні чи «на тижні».
            Максимум 7.
    """
    forecast_days = max(0, min(int(forecast_days), MAX_FORECAST_DAYS))

    params = {
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "timezone": "auto",
    }
    if forecast_days:
        params["daily"] = (
            "temperature_2m_max,temperature_2m_min,weather_code,"
            "precipitation_probability_max"
        )
        # +1, бо перший день у відповіді — сьогоднішній.
        params["forecast_days"] = forecast_days + 1

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
            response = await client.get(
                FORECAST_URL,
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    **params,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as e:
        config.log.exception("Weather lookup failed for %s: %s", city, e)
        return f"Не вдалося отримати погоду для «{city}»."

    name = place.get("name", city)
    country = place.get("country") or ""
    where = f"{name}, {country}".strip(", ")

    current = payload["current"]
    lines = [
        f"{where} зараз: {current['temperature_2m']:.0f}°C "
        f"(відчувається як {current['apparent_temperature']:.0f}°C), "
        f"{WEATHER_CODES.get(current.get('weather_code'), 'невизначено')}, "
        f"вітер {current['wind_speed_10m']:.0f} км/год."
    ]

    daily = payload.get("daily")
    if daily:
        lines.append("Прогноз:")
        for i, date in enumerate(daily["time"]):
            label = "сьогодні" if i == 0 else date
            lines.append(
                f"  {label}: {daily['temperature_2m_min'][i]:.0f}…"
                f"{daily['temperature_2m_max'][i]:.0f}°C, "
                f"{WEATHER_CODES.get(daily['weather_code'][i], 'невизначено')}, "
                f"опади {daily['precipitation_probability_max'][i]}%"
            )

    return "\n".join(lines)
