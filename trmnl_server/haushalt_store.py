"""JSON-backed store for the Haushalts-Board (household task board).

Kept deliberately separate from the SQLAlchemy models used for TRMNL device
state, since this is small, low-concurrency household data that doesn't need
a real schema/migration story. One JSON file, one asyncio lock.

Schema v2 (migrated automatically from v1 on first load):

- Tasks carry a real ISO **date** instead of a weekday index. v1 stored
  ``day`` as 0-6 relative to a week bucket, which silently resolved to the
  wrong real date as soon as the displayed window crossed an ISO week
  boundary — picking "Mo" on a Sunday meant *last* Monday, so the task
  vanished from the board entirely.
- Tasks can carry a ``time`` ("HH:MM"), so the board can sort them
  chronologically together with Google Calendar events.
- The separate "blocks" concept (Sport, Hobby-Tag) is gone — they're just
  regular tasks now (owner "alle" if nobody in particular). The board's
  black-bar look is no longer an opt-in flag; it alternates automatically
  by row position (see plugins/haushalt.py), so it's purely a rendering
  choice with nothing to store here.
- Repeating tasks live as ``templates``, either anchored to a weekday (a
  concrete date each week) or undated (reappears in "Diese Woche" every
  week, for chores that just need doing sometime that week, not on a
  specific day). Concrete instances are materialized forward into a rolling
  horizon, so each occurrence has its own done-state/owner and can be
  edited or deleted individually without affecting the rest — e.g.
  reassigning who's doing it this week doesn't touch future weeks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger(__name__)

STORE_PATH = Path(config.VAR_ROOT) / "haushalt_state.json"
_LOCK = asyncio.Lock()

SCHEMA_VERSION = 2

# "alle" = belongs to the household rather than one person (Sport, Hobby-Tag).
OWNERS = ("jonathan", "katarina", "kids", "alle")

DAY_NAMES = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# How far ahead repeating templates are turned into concrete tasks. Also the
# window the API hands to the UI, so the planning page can cover "next week"
# comfortably while the e-ink board only ever shows the next 3 days.
HORIZON_DAYS = 21

DEFAULT_TASKS = [
    ("jonathan", "Küche kurz durchwischen"),
    ("jonathan", "Müll raus"),
    ("katarina", "Wäsche anstoßen"),
    ("katarina", "Einkaufsliste checken"),
    ("kids", "Spielsachen aufräumen"),
]


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def week_key(d: Optional[date] = None) -> str:
    d = d or date.today()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-w{iso_week}"


def week_label(d: Optional[date] = None) -> str:
    d = d or date.today()
    iso_year, iso_week, _ = d.isocalendar()
    return f"KW {iso_week} · {iso_year}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _empty_state() -> Dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "tasks": [
            {
                "id": _new_id(),
                "text": text,
                "person": person,
                "date": None,
                "time": None,
                "done": False,
                "order": idx,
                "week": week_key(),
                "template_id": None,
            }
            for idx, (person, text) in enumerate(DEFAULT_TASKS)
        ],
        "templates": [],
    }


def _load_raw() -> Dict[str, Any]:
    if not STORE_PATH.exists():
        return _empty_state()
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return _empty_state()

    if data.get("version") != SCHEMA_VERSION:
        # One-way and in place, so keep the original around: if the migration
        # ever mangles real household data there is still something to go back to.
        backup = STORE_PATH.with_name("haushalt_state.v1-backup.json")
        if not backup.exists():
            try:
                backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("Haushalt: v1 state backed up to %s", backup)
            except OSError as exc:
                logger.warning("Haushalt: could not write v1 backup: %s", exc)
        data = _migrate_v1(data)

    data.setdefault("tasks", [])
    data.setdefault("templates", [])
    return data


def _save_raw(data: Dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STORE_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp_path.replace(STORE_PATH)


def _migrate_v1(old: Dict[str, Any]) -> Dict[str, Any]:
    """Convert the week-bucketed v1 file into the flat v2 model.

    Only the *current* week's tasks are carried over — older buckets are
    finished weeks, and future buckets in v1 were an artifact of the board
    renderer pre-creating them. v1 weekday indices are resolved against their
    own week; anything that lands in the past becomes an undated task rather
    than silently reappearing on a date that already went by.
    """
    today = date.today()
    tasks: List[Dict[str, Any]] = []
    order = 0

    current_key = week_key(today)
    bucket = (old.get("weeks") or {}).get(current_key, {})
    monday = today - timedelta(days=today.weekday())

    for person in ("jonathan", "katarina", "kids"):
        for entry in bucket.get(person, []):
            day_idx = entry.get("day")
            task_date = None
            if isinstance(day_idx, int) and 0 <= day_idx <= 6:
                resolved = monday + timedelta(days=day_idx)
                if resolved >= today:
                    task_date = resolved.isoformat()
            tasks.append({
                "id": _new_id(),
                "text": entry.get("text", ""),
                "person": person,
                "date": task_date,
                "time": None,
                "done": bool(entry.get("done")),
                "order": order,
                "week": None if task_date else current_key,
                "template_id": None,
            })
            order += 1

    # v1 blocks (Sport, Hobby-Tag) were weekday-pinned and person-less; they
    # become plain weekly templates owned by the whole household.
    templates: List[Dict[str, Any]] = []
    for block in old.get("blocks") or []:
        weekday = block.get("day")
        if not isinstance(weekday, int) or not (0 <= weekday <= 6):
            continue
        templates.append({
            "id": _new_id(),
            "text": block.get("label", ""),
            "person": "alle",
            "weekday": weekday,
            "time": None,
            "materialized_until": None,
        })

    return {"version": SCHEMA_VERSION, "tasks": tasks, "templates": templates}


# ---------------------------------------------------------------------------
# Repeating templates -> concrete tasks
# ---------------------------------------------------------------------------

def _materialize(data: Dict[str, Any], today: Optional[date] = None) -> bool:
    """Create concrete task instances for repeating templates.

    Only ever moves forward, so deleting a single occurrence doesn't make it
    come back, and a long gap between runs doesn't backfill dates/weeks that
    already passed. Returns True if anything was added.
    """
    today = today or date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    changed = False

    for template in data.get("templates", []):
        weekday = template.get("weekday")
        if isinstance(weekday, int) and 0 <= weekday <= 6:
            changed = _materialize_dated_template(data, template, today, horizon) or changed
        elif weekday is None:
            changed = _materialize_undated_template(data, template, today, horizon) or changed

    return changed


def _materialize_dated_template(data: Dict[str, Any], template: Dict[str, Any], today: date, horizon: date) -> bool:
    """A template anchored to a weekday: one concrete-dated task per occurrence."""
    changed = False
    weekday = template["weekday"]

    raw_until = template.get("materialized_until")
    try:
        start = date.fromisoformat(raw_until) + timedelta(days=1) if raw_until else today
    except (TypeError, ValueError):
        start = today
    if start < today:
        start = today

    cursor = start
    while cursor <= horizon:
        if cursor.weekday() == weekday:
            data["tasks"].append({
                "id": _new_id(),
                "text": template.get("text", ""),
                "person": template.get("person", "alle"),
                "date": cursor.isoformat(),
                "time": template.get("time"),
                "done": False,
                "order": 0,
                "week": None,
                "template_id": template["id"],
            })
            changed = True
        cursor += timedelta(days=1)

    if template.get("materialized_until") != horizon.isoformat():
        template["materialized_until"] = horizon.isoformat()
        changed = True

    return changed


def _materialize_undated_template(data: Dict[str, Any], template: Dict[str, Any], today: date, horizon: date) -> bool:
    """A template with no fixed weekday: one undated ("Diese Woche") task per
    ISO week, so it reappears every week without anyone retyping it — only
    who's doing it needs reassigning.

    Tracks which week keys were already materialized explicitly (rather than
    checking whether a task for that week currently exists), so deleting this
    week's occurrence doesn't just bring it right back on the next refresh.
    """
    changed = False
    weeks_done = set(template.get("materialized_weeks") or [])

    week_keys: List[str] = []
    seen = set()
    cursor = today
    while cursor <= horizon:
        key = week_key(cursor)
        if key not in seen:
            seen.add(key)
            week_keys.append(key)
        cursor += timedelta(days=1)

    for key in week_keys:
        if key in weeks_done:
            continue
        data["tasks"].append({
            "id": _new_id(),
            "text": template.get("text", ""),
            "person": template.get("person", "alle"),
            "date": None,
            "time": template.get("time"),
            "done": False,
            "order": 0,
            "week": key,
            "template_id": template["id"],
        })
        weeks_done.add(key)
        changed = True

    sorted_weeks = sorted(weeks_done)
    if sorted_weeks != (template.get("materialized_weeks") or []):
        template["materialized_weeks"] = sorted_weeks
        changed = True

    return changed


def _prune(data: Dict[str, Any], today: Optional[date] = None) -> bool:
    """Drop finished clutter: dated tasks well in the past, and undated tasks
    from earlier weeks (those were "this week" items that never got done)."""
    today = today or date.today()
    cutoff = (today - timedelta(days=14)).isoformat()
    current_week = week_key(today)

    keep: List[Dict[str, Any]] = []
    for task in data.get("tasks", []):
        task_date = task.get("date")
        if task_date:
            if task_date < cutoff:
                continue
        elif task.get("week") and task["week"] < current_week:
            continue
        keep.append(task)

    changed = len(keep) != len(data.get("tasks", []))
    data["tasks"] = keep
    return changed


def _refresh(data: Dict[str, Any]) -> bool:
    return any([_materialize(data), _prune(data)])


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def _sort_key(task: Dict[str, Any]):
    """Untimed items first (in manual order), then timed items chronologically.

    Mirrors how calendar apps present a day: "anytime today" at the top,
    scheduled things below in the order they happen.
    """
    time_value = task.get("time")
    return (1 if time_value else 0, time_value or "", task.get("order", 0))


def _public(task: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": task["id"],
        "text": task.get("text", ""),
        "person": task.get("person", "alle"),
        "date": task.get("date"),
        "time": task.get("time"),
        "done": bool(task.get("done")),
        "order": task.get("order", 0),
        "repeating": bool(task.get("template_id")),
        "template_id": task.get("template_id"),
    }


async def get_state() -> Dict[str, Any]:
    """Full board state for the mobile page."""
    async with _LOCK:
        data = _load_raw()
        if _refresh(data):
            _save_raw(data)
        return _state_payload(data)


def _state_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    today = date.today()
    tasks = sorted((_public(t) for t in data["tasks"]), key=lambda t: _sort_key(t))
    return {
        "today": today.isoformat(),
        "week_label": week_label(today),
        "week": week_key(today),
        "horizon_days": HORIZON_DAYS,
        "owners": list(OWNERS),
        "tasks": tasks,
        "templates": [
            {
                "id": t["id"],
                "text": t.get("text", ""),
                "person": t.get("person", "alle"),
                "weekday": t.get("weekday"),
                "time": t.get("time"),
            }
            for t in data.get("templates", [])
        ],
    }


async def get_tasks_for_dates(dates: List[date]) -> Dict[date, List[Dict[str, Any]]]:
    """Tasks for specific dates, each list already in display order."""
    async with _LOCK:
        data = _load_raw()
        if _refresh(data):
            _save_raw(data)
        wanted = {d.isoformat(): d for d in dates}
        result: Dict[date, List[Dict[str, Any]]] = {d: [] for d in dates}
        for task in data["tasks"]:
            key = task.get("date")
            if key in wanted:
                result[wanted[key]].append(_public(task))
        for items in result.values():
            items.sort(key=_sort_key)
        return result


async def get_undated_tasks() -> List[Dict[str, Any]]:
    """Undated tasks for the current week ("Diese Woche")."""
    async with _LOCK:
        data = _load_raw()
        if _refresh(data):
            _save_raw(data)
        current = week_key()
        items = [
            _public(t) for t in data["tasks"]
            if not t.get("date") and (t.get("week") or current) == current
        ]
        items.sort(key=_sort_key)
        return items


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _next_order(data: Dict[str, Any], task_date: Optional[str]) -> int:
    siblings = [t.get("order", 0) for t in data["tasks"] if t.get("date") == task_date]
    return (max(siblings) + 1) if siblings else 0


async def add_task(
    text: str,
    person: str,
    task_date: Optional[str] = None,
    time_value: Optional[str] = None,
    repeat_weekly: bool = False,
) -> Dict[str, Any]:
    async with _LOCK:
        data = _load_raw()
        template_id = None

        if repeat_weekly and task_date:
            weekday = date.fromisoformat(task_date).weekday()
            template = {
                "id": _new_id(),
                "text": text,
                "person": person,
                "weekday": weekday,
                "time": time_value,
                # Anchor just before the first occurrence so _materialize picks
                # it up from this date onward instead of duplicating it here.
                "materialized_until": (date.fromisoformat(task_date) - timedelta(days=1)).isoformat(),
            }
            data.setdefault("templates", []).append(template)
            template_id = template["id"]
        elif repeat_weekly:
            # No fixed day: reappears in "Diese Woche" every week instead of
            # a specific date. _materialize_undated_template creates this
            # week's occurrence immediately since materialized_weeks starts empty.
            template = {
                "id": _new_id(),
                "text": text,
                "person": person,
                "weekday": None,
                "time": time_value,
                "materialized_weeks": [],
            }
            data.setdefault("templates", []).append(template)
            template_id = template["id"]
        else:
            data["tasks"].append({
                "id": _new_id(),
                "text": text,
                "person": person,
                "date": task_date,
                "time": time_value,
                "done": False,
                "order": _next_order(data, task_date),
                "week": None if task_date else week_key(),
                "template_id": None,
            })

        if template_id:
            _materialize(data)
        _prune(data)
        _save_raw(data)
        return _state_payload(data)


def _find(data: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    return None


async def update_task(task_id: str, **fields: Any) -> Dict[str, Any]:
    """Patch a single task. Only known fields are applied."""
    async with _LOCK:
        data = _load_raw()
        task = _find(data, task_id)
        if task is not None:
            if "text" in fields and fields["text"] is not None:
                task["text"] = fields["text"]
            if "person" in fields and fields["person"] is not None:
                task["person"] = fields["person"]
            if "time" in fields:
                task["time"] = fields["time"]
            if "date" in fields:
                new_date = fields["date"]
                if new_date != task.get("date"):
                    task["date"] = new_date
                    task["week"] = None if new_date else week_key()
                    task["order"] = _next_order(data, new_date)
        _refresh(data)
        _save_raw(data)
        return _state_payload(data)


async def toggle_task(task_id: str) -> Dict[str, Any]:
    async with _LOCK:
        data = _load_raw()
        task = _find(data, task_id)
        if task is not None:
            task["done"] = not task.get("done", False)
        _refresh(data)
        _save_raw(data)
        return _state_payload(data)


async def delete_task(task_id: str) -> Dict[str, Any]:
    async with _LOCK:
        data = _load_raw()
        data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
        _refresh(data)
        _save_raw(data)
        return _state_payload(data)


async def reorder_tasks(task_ids: List[str]) -> Dict[str, Any]:
    """Apply a new manual order; `task_ids` is one day's list top to bottom."""
    async with _LOCK:
        data = _load_raw()
        position = {task_id: idx for idx, task_id in enumerate(task_ids)}
        for task in data["tasks"]:
            if task["id"] in position:
                task["order"] = position[task["id"]]
        _refresh(data)
        _save_raw(data)
        return _state_payload(data)


async def stop_repeating(template_id: str) -> Dict[str, Any]:
    """Delete a repeat rule and its future occurrences, keeping past ones."""
    async with _LOCK:
        data = _load_raw()
        today = date.today()
        today_iso = today.isoformat()
        current_week = week_key(today)
        data["templates"] = [t for t in data.get("templates", []) if t["id"] != template_id]

        def is_future_occurrence(t: Dict[str, Any]) -> bool:
            if t.get("template_id") != template_id:
                return False
            if t.get("date"):
                return t["date"] >= today_iso
            # Undated occurrences belong to a week instead of a date; keep
            # past weeks (already happened), drop the current/future ones.
            return (t.get("week") or "") >= current_week

        data["tasks"] = [t for t in data["tasks"] if not is_future_occurrence(t)]
        _refresh(data)
        _save_raw(data)
        return _state_payload(data)
