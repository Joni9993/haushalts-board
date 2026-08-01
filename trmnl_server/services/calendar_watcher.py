"""Watches Google Calendar for changes and triggers an immediate board
refresh only when something actually changed — not a blind re-render timer.

This exists because calendar edits happen outside our own API (someone adds
an appointment straight in Google Calendar), so routes/haushalt.py has no
hook to react to. Polling is the pragmatic middle ground here: cheap
(read-only fingerprint compare, no image rendering) and still event-driven
in effect, since the board only re-renders when the fingerprint changes.

A real push-based alternative exists (Google Calendar push notifications /
"watch" channels), but that needs a publicly reachable HTTPS endpoint and
channel renewal every ~7 days — worth it later if 5-minute latency isn't
good enough, not needed for a household board.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

from .. import config, google_calendar
from . import plugins as plugin_service

logger = config.logger

POLL_INTERVAL_SECONDS = 5 * 60  # how often we check for calendar changes

_watcher_task: Optional[asyncio.Task] = None
_last_fingerprint: Optional[str] = None


BOARD_WINDOW_DAYS = 3  # keep in sync with HaushaltPlugin's rolling window


async def _check_once() -> None:
    global _last_fingerprint

    if not google_calendar.is_configured():
        return

    events = await google_calendar.get_events(date.today(), BOARD_WINDOW_DAYS)
    fingerprint = google_calendar.events_fingerprint(events)

    if _last_fingerprint is None:
        # First check after startup: just establish the baseline, don't
        # force a refresh (the plugin already renders once on its own).
        _last_fingerprint = fingerprint
        return

    if fingerprint != _last_fingerprint:
        logger.info("[CalendarWatcher] Calendar change detected, refreshing board")
        _last_fingerprint = fingerprint
        try:
            schedule = plugin_service.get_plugin_schedule("HaushaltPlugin")
            await plugin_service.process_plugin_output(schedule, force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CalendarWatcher] Failed to refresh board: %s", exc)


async def _watch_loop() -> None:
    while True:
        try:
            await _check_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CalendarWatcher] Check failed: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def start() -> None:
    global _watcher_task
    if not google_calendar.is_configured():
        logger.info("[CalendarWatcher] Google Calendar not configured, watcher idle")
        return
    if _watcher_task is None or _watcher_task.done():
        _watcher_task = asyncio.create_task(_watch_loop())
        logger.info(
            "[CalendarWatcher] Started, polling every %s seconds", POLL_INTERVAL_SECONDS
        )


async def stop() -> None:
    global _watcher_task
    if _watcher_task is not None:
        _watcher_task.cancel()
        try:
            await _watcher_task
        except asyncio.CancelledError:
            pass
        _watcher_task = None
