"""Read-only Google Calendar integration for the weekly board.

Design goals:
- Never break the board render if Calendar isn't configured or unreachable —
  everything here fails soft and returns an empty result.
- No web server / redirect URI needed: uses an "installed app" OAuth client,
  authorized once via scripts/setup_google_calendar.py (run locally on a
  machine with a browser — not inside the headless Docker container).
- Read-only scope only; this integration never creates/edits calendar events.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from . import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

CREDENTIALS_PATH = Path(config.VAR_ROOT) / "google_credentials.json"
TOKEN_PATH = Path(config.VAR_ROOT) / "google_token.json"

# Comma-separated calendar IDs to read from; "primary" is the account's main
# calendar. Configure via the CALENDAR_IDS env var if you want to add a
# shared family calendar too.
DEFAULT_CALENDAR_IDS = ["primary"]


def is_configured() -> bool:
    return TOKEN_PATH.exists()


def _load_credentials():
    """Load stored OAuth credentials, refreshing the access token if needed."""
    if not TOKEN_PATH.exists():
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.warning(
            "google-auth libraries not installed; Calendar integration disabled"
        )
        return None

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to refresh Google credentials: %s", exc)
            return None
    return creds


def _calendar_ids() -> List[str]:
    import os

    raw = os.environ.get("CALENDAR_IDS")
    if raw:
        return [c.strip() for c in raw.split(",") if c.strip()]
    return DEFAULT_CALENDAR_IDS


async def get_week_events(monday: date) -> Dict[int, List[str]]:
    """Return {weekday_index (0=Mo..6=So): ["09:00 Zahnarzt", ...]} for the
    ISO week starting at `monday`. Empty dict if not configured or on error.
    """
    import asyncio

    return await asyncio.to_thread(_get_week_events_sync, monday)


def events_fingerprint(events: Dict[int, List[str]]) -> str:
    """Cheap fingerprint so the watcher can tell 'nothing changed' from
    'something changed' without re-rendering the board on every poll."""
    import hashlib
    import json

    serialized = json.dumps(events, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _get_week_events_sync(monday: date) -> Dict[int, List[str]]:
    creds = _load_credentials()
    if not creds:
        return {}

    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning(
            "google-api-python-client not installed; Calendar integration disabled"
        )
        return {}

    week_start = datetime.combine(monday, datetime.min.time(), tzinfo=timezone.utc)
    week_end = week_start + timedelta(days=7)
    result: Dict[int, List[str]] = {i: [] for i in range(7)}

    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        for cal_id in _calendar_ids():
            events = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=week_start.isoformat(),
                    timeMax=week_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=100,
                )
                .execute()
                .get("items", [])
            )
            for event in events:
                start = event.get("start", {})
                start_raw = start.get("dateTime") or start.get("date")
                if not start_raw:
                    continue
                try:
                    if "T" in start_raw:
                        dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                        day_idx = (dt.date() - monday).days
                        time_label = dt.strftime("%H:%M")
                    else:
                        event_date = date.fromisoformat(start_raw)
                        day_idx = (event_date - monday).days
                        time_label = "ganztägig"
                except ValueError:
                    continue
                if 0 <= day_idx <= 6:
                    title = event.get("summary", "(ohne Titel)")
                    result[day_idx].append(f"{time_label} {title}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch Google Calendar events: %s", exc)
        return {i: [] for i in range(7)}

    return result
