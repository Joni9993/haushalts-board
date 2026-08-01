"""API for the Haushalts-Board: tasks (add/toggle/delete) and weekly blocks
(Sport, Hobby-Tag, etc. — drag & drop between weekdays).

Kept separate from the TRMNL device-protocol routes in api.py. This is what
the mobile page (web/haushalt.html) talks to; the e-ink display itself only
ever sees the rendered image produced by plugins/haushalt.py.

Every mutating endpoint triggers an immediate re-render of the board plugin
instead of waiting for the scheduled refresh interval — that's the
event-driven update: the device always gets the latest image on its next
poll, with no fixed "every N minutes" render in between.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import haushalt_store
from ..services import plugins as plugin_service

router = APIRouter(prefix="/api/haushalt", tags=["haushalt"])

VALID_COLUMNS = {"jonathan", "katarina", "kids"}
VALID_PEOPLE = {"jonathan", "katarina"}


class NewTask(BaseModel):
    text: str
    day: int | None = None  # 0=Mo ... 6=So, None = kein bestimmter Tag


class SetTaskDay(BaseModel):
    day: int | None = None


class NewBlock(BaseModel):
    label: str
    day: int  # 0=Mo ... 6=So


class MoveBlock(BaseModel):
    day: int


class RenameBlock(BaseModel):
    label: str


def _check_column(column: str) -> None:
    if column not in VALID_COLUMNS:
        raise HTTPException(status_code=404, detail=f"Unknown column '{column}'")


def _check_day(day: int) -> None:
    if not (0 <= day <= 6):
        raise HTTPException(status_code=400, detail="day muss zwischen 0 (Mo) und 6 (So) liegen")


async def _trigger_board_refresh() -> None:
    """Re-render the board image right now instead of waiting for the timer."""
    try:
        schedule = plugin_service.get_plugin_schedule("HaushaltPlugin")
        await plugin_service.process_plugin_output(schedule)
    except Exception:
        # Rendering is best-effort here; the API response to the phone should
        # never fail just because the e-ink refresh hiccuped.
        pass


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@router.get("/state")
async def get_state():
    return await haushalt_store.get_current_week()


@router.post("/{column}/tasks")
async def add_task(column: str, payload: NewTask):
    _check_column(column)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Task text darf nicht leer sein")
    if payload.day is not None:
        _check_day(payload.day)
    result = await haushalt_store.add_task(column, text, payload.day)
    await _trigger_board_refresh()
    return result


@router.post("/{column}/tasks/{idx}/day")
async def set_task_day(column: str, idx: int, payload: SetTaskDay):
    _check_column(column)
    if payload.day is not None:
        _check_day(payload.day)
    result = await haushalt_store.set_task_day(column, idx, payload.day)
    await _trigger_board_refresh()
    return result


@router.post("/{column}/tasks/{idx}/toggle")
async def toggle_task(column: str, idx: int):
    _check_column(column)
    result = await haushalt_store.toggle_task(column, idx)
    await _trigger_board_refresh()
    return result


@router.delete("/{column}/tasks/{idx}")
async def delete_task(column: str, idx: int):
    _check_column(column)
    result = await haushalt_store.delete_task(column, idx)
    await _trigger_board_refresh()
    return result


@router.post("/{person}/tasks/{idx}/recurring")
async def toggle_recurring(person: str, idx: int):
    if person not in VALID_PEOPLE:
        raise HTTPException(status_code=404, detail=f"'{person}' kann nicht wiederkehrend sein (nur jonathan/katarina)")
    result = await haushalt_store.toggle_recurring(person, idx)
    await _trigger_board_refresh()
    return result


# ---------------------------------------------------------------------------
# Weekly blocks (Sport, Hobby-Tag, ... — drag & drop planner)
# ---------------------------------------------------------------------------

@router.get("/blocks")
async def get_blocks():
    return {"blocks": await haushalt_store.get_blocks()}


@router.post("/blocks")
async def add_block(payload: NewBlock):
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Block-Name darf nicht leer sein")
    _check_day(payload.day)
    blocks = await haushalt_store.add_block(label, payload.day)
    await _trigger_board_refresh()
    return {"blocks": blocks}


@router.post("/blocks/{block_id}/move")
async def move_block(block_id: str, payload: MoveBlock):
    _check_day(payload.day)
    blocks = await haushalt_store.move_block(block_id, payload.day)
    await _trigger_board_refresh()
    return {"blocks": blocks}


@router.post("/blocks/{block_id}/rename")
async def rename_block(block_id: str, payload: RenameBlock):
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Block-Name darf nicht leer sein")
    blocks = await haushalt_store.rename_block(block_id, label)
    await _trigger_board_refresh()
    return {"blocks": blocks}


@router.delete("/blocks/{block_id}")
async def delete_block(block_id: str):
    blocks = await haushalt_store.delete_block(block_id)
    await _trigger_board_refresh()
    return {"blocks": blocks}
