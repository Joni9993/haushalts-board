"""Read-only Google Calendar integration for the weekly board.

Design goals:
- Never break the board render if Calendar isn't configured or unreachable —
  everything here fails soft and returns an empty result.
- No web server / redirect URI needed: uses an "installed app" OAuth client,
  authorized once per family member via scripts/setup_google_calendar.py (run
  locally on a machine with a browser — not inside the headless Docker
  container).
- Read-only scope only; this integration never creates/edits calendar events.

Multiple, independent Google accounts (e.g. one per family member) are
supported without any calendar-sharing setup: every account gets authorized
separately and its own token file, and events from all of them are merged.
The OAuth *client* (var/google_credentials.json) is shared — that identifies
the application, not the end user — but each person's login produces a
distinct token tied to their own account.

- var/google_token.json is the default/first account.
- var/google_token_<label>.json adds another account; the label is free-form
  (e.g. "katarina") and only used in log messages.

Dropping a new token file into var/ (and restarting) is the entire "setup" —
no extra environment variable enumerating accounts is needed.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

CREDENTIALS_PATH = Path(config.VAR_ROOT) / "google_credentials.json"
TOKEN_PATH = Path(config.VAR_ROOT) / "google_token.json"

# Comma-separated calendar IDs to read from within the *default* account;
# "primary" is that account's main calendar. Configure via the CALENDAR_IDS
# env var if you want to add a calendar shared *into* that same account.
# Additional accounts (var/google_token_<label>.json) always just read their
# own "primary" — if someone shared a calendar with them, add another account
# for that instead of trying to fold it into this list.
DEFAULT_CALENDAR_IDS = ["primary"]


def _discover_accounts() -> List[Tuple[str, Path]]:
    """Every configured Google account: the default token plus any
    var/google_token_<label>.json for additional family members."""
    accounts: List[Tuple[str, Path]] = []
    if TOKEN_PATH.exists():
        accounts.append(("default", TOKEN_PATH))
    var_root = Path(config.VAR_ROOT)
    for path in sorted(var_root.glob("google_token_*.json")):
        label = path.stem[len("google_token_"):]
        accounts.append((label, path))
    return accounts


def is_configured() -> bool:
    return bool(_discover_accounts())


def _load_credentials_from(token_path: Path, label: str):
    """Load stored OAuth credentials, refreshing the access token if needed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.warning(
            "google-auth libraries not installed; Calendar integration disabled"
        )
        return None

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to refresh Google credentials for %s: %s", label, exc)
            return None
    return creds


def _calendar_ids_for(label: str) -> List[str]:
    if label != "default":
        return ["primary"]
    import os

    raw = os.environ.get("CALENDAR_IDS")
    if raw:
        return [c.strip() for c in raw.split(",") if c.strip()]
    return DEFAULT_CALENDAR_IDS


async def get_events(start: date, days: int) -> Dict[int, List[dict]]:
    """Return {offset (0..days-1): [{"time": "09:00"|None, "title": ...}, ...]}
    for the `days` calendar days starting at `start`, merged across every
    configured account. Empty dict if nothing is configured.

    Time is kept as a separate field rather than baked into the title so the
    board can sort calendar events and timed tasks into one chronological list.
    """
    import asyncio

    return await asyncio.to_thread(_get_events_sync, start, days)


def events_fingerprint(events: Dict[int, List[dict]]) -> str:
    """Cheap fingerprint so the watcher can tell 'nothing changed' from
    'something changed' without re-rendering the board on every poll."""
    import hashlib
    import json

    serialized = json.dumps(events, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _fetch_account_events(
    label: str, token_path: Path, range_start: datetime, range_end: datetime, days: int
) -> Optional[Dict[int, List[dict]]]:
    """Fetch one account's events. Returns None on failure so the caller can
    skip just this account instead of losing every account's events."""
    creds = _load_credentials_from(token_path, label)
    if not creds:
        return None

    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning(
            "google-api-python-client not installed; Calendar integration disabled"
        )
        return None

    result: Dict[int, List[dict]] = {i: [] for i in range(days)}
    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        for cal_id in _calendar_ids_for(label):
            events = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=range_start.isoformat(),
                    timeMax=range_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=100,
                )
                .execute()
                .get("items", [])
            )
            for event in events:
                event_start = event.get("start", {})
                start_raw = event_start.get("dateTime") or event_start.get("date")
                if not start_raw:
                    continue
                try:
                    if "T" in start_raw:
                        dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                        day_idx = (dt.date() - range_start.date()).days
                        time_label = dt.strftime("%H:%M")
                    else:
                        event_date = date.fromisoformat(start_raw)
                        day_idx = (event_date - range_start.date()).days
                        time_label = None  # all-day event
                except ValueError:
                    continue
                if 0 <= day_idx < days:
                    title = event.get("summary", "(ohne Titel)")
                    result[day_idx].append({"time": time_label, "title": title})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch Google Calendar events for %s: %s", label, exc)
        return None

    return result


def _get_events_sync(start: date, days: int) -> Dict[int, List[dict]]:
    accounts = _discover_accounts()
    if not accounts:
        return {}

    range_start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    range_end = range_start + timedelta(days=days)
    merged: Dict[int, List[dict]] = {i: [] for i in range(days)}

    for label, token_path in accounts:
        account_events = _fetch_account_events(label, token_path, range_start, range_end, days)
        if account_events is None:
            continue  # this account failed; the others still count
        for day_idx, items in account_events.items():
            merged[day_idx].extend(items)

    for items in merged.values():
        items.sort(key=lambda e: (1 if e["time"] else 0, e["time"] or ""))

    return merged
