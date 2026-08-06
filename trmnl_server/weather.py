"""Read-only weather lookup for the household board's footer panel.

Uses Open-Meteo (https://open-meteo.com) — free, no API key, no signup.
Fails soft like google_calendar.py: any error just means the board renders
without the weather panel, never a broken board.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# 74626 Bretzfeld, Baden-Württemberg. Override via WEATHER_LAT/WEATHER_LON
# env vars if the board ever moves or covers a different household.
DEFAULT_LAT = 49.17944
DEFAULT_LON = 9.43833

# WMO weather codes (used by Open-Meteo) collapsed into the handful of icon
# categories plugins/haushalt.py knows how to draw. Full table:
# https://open-meteo.com/en/docs -> "WMO Weather interpretation codes"
_CODE_CATEGORY = {
    0: "sun",
    1: "sun_cloud", 2: "sun_cloud",
    3: "cloud",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
    61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
    80: "rain", 81: "rain", 82: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow",
    95: "storm", 96: "storm", 99: "storm",
}

_CATEGORY_LABEL = {
    "sun": "Sonnig",
    "sun_cloud": "Leicht bewölkt",
    "cloud": "Bewölkt",
    "fog": "Neblig",
    "drizzle": "Nieselregen",
    "rain": "Regen",
    "snow": "Schnee",
    "storm": "Gewitter",
}


def _coords() -> tuple[float, float]:
    try:
        lat = float(os.environ.get("WEATHER_LAT", DEFAULT_LAT))
        lon = float(os.environ.get("WEATHER_LON", DEFAULT_LON))
    except ValueError:
        lat, lon = DEFAULT_LAT, DEFAULT_LON
    return lat, lon


async def get_today() -> Optional[dict]:
    """Return today's weather for the board:
    {"temp_now": 18, "category": "sun", "label": "Sonnig",
     "temp_min": 12, "temp_max": 22, "precip_prob": 20}
    or None if the lookup failed (network, malformed response, etc.) — the
    board simply omits the panel in that case rather than breaking.
    """
    lat, lon = _coords()
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=Europe%2FBerlin&forecast_days=1"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        current = data["current"]
        daily = data["daily"]
        category = _CODE_CATEGORY.get(current["weather_code"], "cloud")

        return {
            "temp_now": round(current["temperature_2m"]),
            "category": category,
            "label": _CATEGORY_LABEL[category],
            "temp_min": round(daily["temperature_2m_min"][0]),
            "temp_max": round(daily["temperature_2m_max"][0]),
            "precip_prob": daily["precipitation_probability_max"][0],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch weather: %s", exc)
        return None
