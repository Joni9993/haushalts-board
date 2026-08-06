"""Hardcoded household waste-collection calendar for the board's footer panel.

74626 Waldbach (Bretzfeld, Hohenlohekreis) — no public API/iCal feed exists
for this, so unlike weather.py/google_calendar.py it cannot update itself.

**Needs updating every December** for the following year: Abfallwirtschaft
Hohenlohekreis's "Termine Leerungen" page lets you export the next year's
dates as text per address —
https://www.abfallwirtschaft-hohenlohekreis.de/infos-beratung/termine-leerungen
— copy those into _RAW_DATES below and bump YEAR. Once every category's last
date is in the past, next_pickups() quietly returns an empty list rather than
showing stale dates — if the "Müll" panel on the board goes blank, that's the
signal this file is now a year out of date.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

YEAR = 2026

_RAW_DATES: Dict[str, List[str]] = {
    "Gelb": [
        "07.01", "19.01", "30.01", "13.02", "27.02", "13.03", "28.03", "11.04",
        "25.04", "09.05", "23.05", "06.06", "19.06", "03.07", "17.07", "31.07",
        "14.08", "28.08", "11.09", "25.09", "09.10", "23.10", "06.11", "20.11",
        "04.12", "17.12",
    ],
    "Bio": [
        "08.01", "21.01", "03.02", "16.02", "02.03", "16.03", "28.03", "13.04",
        "25.04", "09.05", "26.05", "09.06", "15.06", "22.06", "29.06", "06.07",
        "13.07", "20.07", "27.07", "03.08", "10.08", "17.08", "24.08", "31.08",
        "14.09", "28.09", "12.10", "26.10", "09.11", "23.11", "07.12", "19.12",
    ],
    "Restmüll": [
        "15.01", "28.01", "10.02", "23.02", "09.03", "23.03", "07.04", "20.04",
        "04.05", "19.05", "01.06", "15.06", "29.06", "13.07", "27.07", "10.08",
        "24.08", "07.09", "21.09", "05.10", "19.10", "02.11", "16.11", "30.11",
        "14.12", "28.12",
    ],
    "Papier": [
        "24.01", "20.02", "20.03", "17.04", "18.05", "12.06", "10.07", "07.08",
        "04.09", "02.10", "30.10", "27.11", "23.12",
    ],
}


def _parse(day_month: str) -> date:
    day, month = day_month.split(".")[:2]
    return date(YEAR, int(month), int(day))


DATES: Dict[str, List[date]] = {
    category: sorted(_parse(d) for d in raw)
    for category, raw in _RAW_DATES.items()
}


def next_pickups(today: Optional[date] = None) -> List[dict]:
    """Return [{"category": "Papier", "date": date(...)}, ...] — the next
    upcoming pickup for every category that still has one this year, soonest
    first. Empty once every category has run out (see module docstring)."""
    today = today or date.today()
    upcoming = []
    for category, dates in DATES.items():
        for d in dates:
            if d >= today:
                upcoming.append({"category": category, "date": d})
                break
    upcoming.sort(key=lambda item: (item["date"], item["category"]))
    return upcoming
