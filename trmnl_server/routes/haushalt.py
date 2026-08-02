"""API for the Haushalts-Board: tasks with real dates, optional times,
highlighting, and weekly repeat rules.

Kept separate from the TRMNL device-protocol routes in api.py. This is what
the mobile page (web/haushalt.html) talks to; the e-ink display itself only
ever sees the rendered image produced by plugins/haushalt.py.

Every mutating endpoint triggers an immediate re-render of the board plugin
instead of waiting for the scheduled refresh interval — that's the
event-driven update: the device always gets the latest image on its next
poll, with no fixed "every N minutes" render in between.
"""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import haushalt_store
from ..services import plugins as plugin_service

router = APIRouter(prefix="/api/haushalt", tags=["haushalt"])

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class NewTask(BaseModel):
    text: str
    person: str
    date: str | None = None          # ISO "YYYY-MM-DD", None = kein fester Tag
    time: str | None = None          # "HH:MM"
    highlight: bool = False
    repeat_weekly: bool = False


class PatchTask(BaseModel):
    text: str | None = None
    person: str | None = None
    date: str | None = None
    time: str | None = None
    highlight: bool | None = None
    # Explicit clear flags, since None already means "leave unchanged" above.
    clear_date: bool = False
    clear_time: bool = False


class Reorder(BaseModel):
    ids: list[str]


def _check_person(person: str) -> None:
    if person not in haushalt_store.OWNERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte Person '{person}'. Erlaubt: {', '.join(haushalt_store.OWNERS)}",
        )


def _check_date(value: str | None) -> None:
    if value is None:
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="date muss im Format YYYY-MM-DD sein") from None


def _check_time(value: str | None) -> None:
    if value is None:
        return
    if not _TIME_RE.match(value):
        raise HTTPException(status_code=400, detail="time muss im Format HH:MM sein")


async def _trigger_board_refresh() -> None:
    """Re-render the board image right now instead of waiting for the timer.

    force=True is required here: process_plugin_output() is normally a
    TTL-gated cache check for the periodic background refresh loop, and the
    Haushalt plugin's TTL is 6 hours, so without forcing it this would almost
    always no-op and keep serving the stale cached render.
    """
    try:
        schedule = plugin_service.get_plugin_schedule("HaushaltPlugin")
        await plugin_service.process_plugin_output(schedule, force=True)
    except Exception:
        # Rendering is best-effort here; the API response to the phone should
        # never fail just because the e-ink refresh hiccuped.
        pass


@router.get("/state")
async def get_state():
    return await haushalt_store.get_state()


@router.post("/tasks")
async def add_task(payload: NewTask):
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text darf nicht leer sein")
    _check_person(payload.person)
    _check_date(payload.date)
    _check_time(payload.time)
    if payload.repeat_weekly and not payload.date:
        raise HTTPException(
            status_code=400,
            detail="Wöchentliche Wiederholung braucht einen Tag (der Wochentag ergibt sich daraus)",
        )

    result = await haushalt_store.add_task(
        text=text,
        person=payload.person,
        task_date=payload.date,
        time_value=payload.time,
        highlight=payload.highlight,
        repeat_weekly=payload.repeat_weekly,
    )
    await _trigger_board_refresh()
    return result


@router.patch("/tasks/{task_id}")
async def patch_task(task_id: str, payload: PatchTask):
    if payload.person is not None:
        _check_person(payload.person)
    _check_date(payload.date)
    _check_time(payload.time)

    fields: dict = {}
    if payload.text is not None:
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Text darf nicht leer sein")
        fields["text"] = text
    if payload.person is not None:
        fields["person"] = payload.person
    if payload.highlight is not None:
        fields["highlight"] = payload.highlight
    if payload.clear_date:
        fields["date"] = None
    elif payload.date is not None:
        fields["date"] = payload.date
    if payload.clear_time:
        fields["time"] = None
    elif payload.time is not None:
        fields["time"] = payload.time

    result = await haushalt_store.update_task(task_id, **fields)
    await _trigger_board_refresh()
    return result


@router.post("/tasks/{task_id}/toggle")
async def toggle_task(task_id: str):
    result = await haushalt_store.toggle_task(task_id)
    await _trigger_board_refresh()
    return result


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    result = await haushalt_store.delete_task(task_id)
    await _trigger_board_refresh()
    return result


@router.post("/tasks/reorder")
async def reorder_tasks(payload: Reorder):
    result = await haushalt_store.reorder_tasks(payload.ids)
    await _trigger_board_refresh()
    return result


@router.delete("/templates/{template_id}")
async def stop_repeating(template_id: str):
    """Stop a weekly repeat: removes the rule and its future occurrences."""
    result = await haushalt_store.stop_repeating(template_id)
    await _trigger_board_refresh()
    return result
