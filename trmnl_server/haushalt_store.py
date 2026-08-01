"""Simple JSON-backed store for the Haushalts-Board (household chore board).

Kept deliberately separate from the SQLAlchemy models used for TRMNL device
state, since this is small, low-concurrency household data that doesn't need
a real schema/migration story. One JSON file, one asyncio lock.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from . import config

STORE_PATH = Path(config.VAR_ROOT) / "haushalt_state.json"
_LOCK = asyncio.Lock()

PEOPLE = ("jonathan", "katarina")
ALL_COLUMNS = ("jonathan", "katarina", "kids")

DEFAULT_TASKS = {
    "jonathan": ["Küche kurz durchwischen", "Müll raus"],
    "katarina": ["Wäsche anstoßen", "Einkaufsliste checken"],
    "kids": ["Spielsachen aufräumen"],
}


def _week_key(d: date | None = None) -> str:
    d = d or date.today()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-w{iso_week}"


def week_label(d: date | None = None) -> str:
    d = d or date.today()
    iso_year, iso_week, _ = d.isocalendar()
    return f"KW {iso_week} · {iso_year}"


DAY_NAMES = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

DEFAULT_BLOCKS = [
    {"label": "Hobby-Tag", "day": 1},   # Di
    {"label": "Sport", "day": 3},       # Do
]


def _empty_state() -> Dict[str, Any]:
    return {
        "weeks": {},
        "recurring": {"jonathan": [], "katarina": []},
        "blocks": [
            {"id": uuid.uuid4().hex[:8], **b} for b in DEFAULT_BLOCKS
        ],
    }


def _week_payload(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    return {
        "key": key,
        "label": week_label(),
        "recurring": data["recurring"],
        **data["weeks"][key],
    }


def _load_raw() -> Dict[str, Any]:
    if not STORE_PATH.exists():
        return _empty_state()
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    data.setdefault("weeks", {})
    data.setdefault("recurring", {"jonathan": [], "katarina": []})
    data.setdefault("blocks", [])
    return data


def _save_raw(data: Dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STORE_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp_path.replace(STORE_PATH)


def _ensure_week(data: Dict[str, Any], key: str) -> None:
    if key in data["weeks"]:
        return
    data["weeks"][key] = {
        "jonathan": [
            {"text": t, "done": False, "day": None}
            for t in [*DEFAULT_TASKS["jonathan"], *data["recurring"].get("jonathan", [])]
        ],
        "katarina": [
            {"text": t, "done": False, "day": None}
            for t in [*DEFAULT_TASKS["katarina"], *data["recurring"].get("katarina", [])]
        ],
        "kids": [{"text": t, "done": False, "day": None} for t in DEFAULT_TASKS["kids"]],
    }


async def get_current_week() -> Dict[str, Any]:
    """Return this week's board, creating it (with recurring tasks) if needed."""
    async with _LOCK:
        data = _load_raw()
        key = _week_key()
        _ensure_week(data, key)
        _save_raw(data)
        return _week_payload(data, key)


async def add_task(column: str, text: str, day: int | None = None) -> Dict[str, Any]:
    async with _LOCK:
        data = _load_raw()
        key = _week_key()
        _ensure_week(data, key)
        data["weeks"][key][column].append({"text": text, "done": False, "day": day})
        _save_raw(data)
        return _week_payload(data, key)


async def toggle_task(column: str, idx: int) -> Dict[str, Any]:
    async with _LOCK:
        data = _load_raw()
        key = _week_key()
        _ensure_week(data, key)
        tasks = data["weeks"][key][column]
        if 0 <= idx < len(tasks):
            tasks[idx]["done"] = not tasks[idx]["done"]
        _save_raw(data)
        return _week_payload(data, key)


async def delete_task(column: str, idx: int) -> Dict[str, Any]:
    async with _LOCK:
        data = _load_raw()
        key = _week_key()
        _ensure_week(data, key)
        tasks = data["weeks"][key][column]
        if 0 <= idx < len(tasks):
            tasks.pop(idx)
        _save_raw(data)
        return _week_payload(data, key)


async def toggle_recurring(person: str, idx: int) -> Dict[str, Any]:
    """Only jonathan/katarina tasks can be marked recurring (kids tasks reset weekly)."""
    async with _LOCK:
        data = _load_raw()
        key = _week_key()
        _ensure_week(data, key)
        tasks = data["weeks"][key][person]
        if not (0 <= idx < len(tasks)):
            return _week_payload(data, key)
        text = tasks[idx]["text"]
        recurring: List[str] = data["recurring"].setdefault(person, [])
        if text in recurring:
            recurring.remove(text)
        else:
            recurring.append(text)
        _save_raw(data)
        return _week_payload(data, key)


async def set_task_day(column: str, idx: int, day: int | None) -> Dict[str, Any]:
    async with _LOCK:
        data = _load_raw()
        key = _week_key()
        _ensure_week(data, key)
        tasks = data["weeks"][key][column]
        if 0 <= idx < len(tasks):
            tasks[idx]["day"] = day
        _save_raw(data)
        return _week_payload(data, key)


async def is_recurring(person: str, text: str) -> bool:
    async with _LOCK:
        data = _load_raw()
        return text in data.get("recurring", {}).get(person, [])


async def get_blocks() -> List[Dict[str, Any]]:
    """Weekly fixed blocks (Sport, Hobby-Tag, ...). Not tied to a specific
    week — they persist and carry forward until dragged elsewhere, matching
    'always there, but rearrangeable' rather than a per-week snapshot."""
    async with _LOCK:
        data = _load_raw()
        return data.get("blocks", [])


async def add_block(label: str, day: int) -> List[Dict[str, Any]]:
    async with _LOCK:
        data = _load_raw()
        data.setdefault("blocks", []).append(
            {"id": uuid.uuid4().hex[:8], "label": label, "day": day}
        )
        _save_raw(data)
        return data["blocks"]


async def move_block(block_id: str, day: int) -> List[Dict[str, Any]]:
    async with _LOCK:
        data = _load_raw()
        for block in data.get("blocks", []):
            if block["id"] == block_id:
                block["day"] = day
                break
        _save_raw(data)
        return data.get("blocks", [])


async def rename_block(block_id: str, label: str) -> List[Dict[str, Any]]:
    async with _LOCK:
        data = _load_raw()
        for block in data.get("blocks", []):
            if block["id"] == block_id:
                block["label"] = label
                break
        _save_raw(data)
        return data.get("blocks", [])


async def delete_block(block_id: str) -> List[Dict[str, Any]]:
    async with _LOCK:
        data = _load_raw()
        data["blocks"] = [b for b in data.get("blocks", []) if b["id"] != block_id]
        _save_raw(data)
        return data["blocks"]
