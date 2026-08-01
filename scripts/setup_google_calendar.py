#!/usr/bin/env python
"""One-time Google Calendar authorization for the Haushalts-Board.

Run this LOCALLY on a machine with a browser (your PC/laptop) — not inside
the headless Docker container. It opens a browser window, asks you to log
in with your Google account and grant read-only Calendar access, then saves
a token file that the server (running in Docker) reads.

Steps before running this:
1. Go to https://console.cloud.google.com/ -> create a project (or reuse one).
2. Enable the "Google Calendar API" for that project.
3. Create OAuth credentials: "OAuth client ID" -> Application type "Desktop app".
4. Download the JSON, save it as `var/google_credentials.json` in this repo
   (create the `var` folder if it doesn't exist yet).
5. Run: pip install google-auth-oauthlib google-api-python-client
6. Run: python scripts/setup_google_calendar.py
7. A browser window opens -> log in -> allow read-only calendar access.
8. This creates `var/google_token.json`.
9. Copy that file into the Docker volume the server uses for /app/var
   (e.g. `docker cp var/google_token.json haushalts-board:/app/var/`),
   or just place it in `var/` before your first `docker compose up` if you
   bind-mount `./var:/app/var` instead of a named volume.
"""

from __future__ import annotations

import sys
from pathlib import Path

VAR_DIR = Path(__file__).resolve().parent.parent / "var"
CREDENTIALS_PATH = VAR_DIR / "google_credentials.json"
TOKEN_PATH = VAR_DIR / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main() -> None:
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

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    VAR_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved {TOKEN_PATH}.")
    print("Copy this file into the server's var/ directory (see docstring above) and restart the container.")


if __name__ == "__main__":
    main()
