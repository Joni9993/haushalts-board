#!/usr/bin/env python
"""One-time Google Calendar authorization for the Haushalts-Board.

Run this LOCALLY on a machine with a browser (your PC/laptop) — not inside
the headless Docker container. It opens a browser window, asks you to log
in with your Google account and grant read-only Calendar access, then saves
a token file that the server (running in Docker) reads.

Steps before running this (once, for the whole family — the OAuth *client*
below identifies the application, not the end user, so every family member
authorizes against the same credentials file with their own Google login):
1. Go to https://console.cloud.google.com/ -> create a project (or reuse one).
2. Enable the "Google Calendar API" for that project.
3. Create OAuth credentials: "OAuth client ID" -> Application type "Desktop app".
4. Download the JSON, save it as `var/google_credentials.json` in this repo
   (create the `var` folder if it doesn't exist yet).
5. On the OAuth consent screen, add every family member's Google account
   under "Test users" — otherwise their login gets rejected with
   "access_denied" (the app stays unverified/"Testing").
6. Run: pip install google-auth-oauthlib google-api-python-client

Usage:
  python scripts/setup_google_calendar.py             # first / only account
  python scripts/setup_google_calendar.py katarina     # an additional family
                                                        # member's own account

Each run:
7. Opens a browser window -> log in with THAT person's Google account ->
   allow read-only calendar access.
8. Saves `var/google_token.json` (no name given) or
   `var/google_token_<name>.json` (name given).
9. Copy that file into the Docker volume the server uses for /app/var
   (e.g. `docker cp var/google_token_katarina.json haushalts-board:/app/var/`),
   or just place it in `var/` before your first `docker compose up` if you
   bind-mount `./var:/app/var` instead of a named volume.

The server picks up every token file in var/ automatically (see
trmnl_server/google_calendar.py) and merges all of their calendars — no
further configuration needed, just restart the container after copying a
new or refreshed token file in.
"""

from __future__ import annotations

import sys
from pathlib import Path

VAR_DIR = Path(__file__).resolve().parent.parent / "var"
CREDENTIALS_PATH = VAR_DIR / "google_credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main() -> None:
    label = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else None
    token_path = VAR_DIR / (f"google_token_{label}.json" if label else "google_token.json")

    if not CREDENTIALS_PATH.exists():
        print(f"Missing {CREDENTIALS_PATH}.")
        print("Download OAuth client credentials (Desktop app type) from")
        print("https://console.cloud.google.com/apis/credentials and save them there.")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing dependency. Run: pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    if token_path.exists():
        print(f"Warning: {token_path} already exists and will be overwritten.")
        print("(That's fine for refreshing the same person's access; if you meant to add a")
        print(" DIFFERENT family member, re-run with their name as an argument instead, e.g.:")
        print("   python scripts/setup_google_calendar.py katarina")
        answer = input("Continue and overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted, nothing was changed.")
            sys.exit(0)

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    VAR_DIR.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved {token_path}.")
    print("Copy this file into the server's var/ directory (see docstring above) and restart the container.")


if __name__ == "__main__":
    main()
