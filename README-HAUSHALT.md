# Haushalts-Board – Self-Hosted TRMNL Setup

Dieses Repo ist ein Fork von [usetrmnl/byos_fastapi](https://github.com/usetrmnl/byos_fastapi),
erweitert um ein eigenes Plugin (`Haushalts-Board`) für den Kühlschrank-Bildschirm
(TRMNL 7.5" OG DIY Kit, 800×480). Die Basis-README (`README.md`) vom Original-Projekt
gilt weiterhin für alles Generelle (Setup, andere Plugins, Firmware); hier steht nur,
was für unser Vorhaben speziell dazugekommen ist.

## Was wurde hinzugefügt

- `trmnl_server/haushalt_store.py` – JSON-Datei-Speicher (`var/haushalt_state.json`)
  für die Aufgaben von Jonathan, Katarina und den Kindern, wochenweise (KW-Schlüssel),
  inkl. "wiederkehrend"-Markierung, plus die festen Wochen-Blöcke (Sport,
  Hobby-Tag, ...).
- `trmnl_server/plugins/haushalt.py` – Plugin, das das Board als 800×480-Bild
  rendert (Wochen-Blöcke-Leiste oben, zwei Spalten Jonathan/Katarina, Kinder-Leiste
  unten).
- `trmnl_server/routes/haushalt.py` – JSON-API unter `/api/haushalt/...` zum
  Eintragen, Abhaken, Löschen und Wiederkehrend-Markieren von Aufgaben, sowie
  Erstellen/Verschieben/Umbenennen/Löschen der Wochen-Blöcke.
- `web/haushalt.html` – Handy-taugliche Seite mit Wochenplaner (Drag & Drop,
  funktioniert per Touch – nicht über natives HTML5-Drag&Drop, das auf
  Smartphones nicht greift, sondern über Pointer-Events) und den drei
  Aufgaben-Spalten.
- `Dockerfile`, `docker-compose.yml` – Deployment-Setup.

## Event-getriebenes Update statt festem Timer

Das Board rendert sich **nicht** mehr alle 10 Minuten neu. Stattdessen löst jeder
Änderungs-Endpunkt in `routes/haushalt.py` sofort `process_plugin_output()` für das
Haushalt-Plugin aus – das Bild ist also innerhalb von Sekunden nach einer Änderung
**auf der Handy-Seite** aktuell. `REFRESH_INTERVAL` im Plugin (6 Stunden) ist nur noch
ein Sicherheitsnetz, falls die JSON-Datei mal außerhalb der API verändert wird.

**Google-Kalender-Änderungen sind ein Sonderfall:** Ein Termin, der direkt in Google
Kalender eingetragen wird (nicht über unsere Handy-Seite), geht an der API vorbei –
davon bekommt der Server sonst nichts mit. Dafür gibt's
`trmnl_server/services/calendar_watcher.py`: ein Hintergrund-Check alle 5 Minuten,
der nur einen Fingerprint der Kalender-Termine vergleicht (kein Rendern, kaum
Aufwand) und **nur bei einer echten Änderung** sofort einen Board-Refresh auslöst.
Bleibt automatisch inaktiv, solange kein Kalender verbunden ist (siehe unten).

Eine echte Push-Lösung (Google "watch"-Kanäle, die euren Server aktiv benachrichtigen)
wäre technisch möglich über euren bestehenden Cloudflare-Tunnel, bräuchte aber eine
öffentlich erreichbare HTTPS-Adresse und alle ~7 Tage eine Kanal-Erneuerung – für ein
Haushalts-Board Overkill, der 5-Minuten-Check reicht hier.

**Wichtig zu wissen:** Das eigentliche *Abholen* des Bildes durch das Gerät folgt
weiterhin seinem eigenen Poll-Intervall (in der Geräte-Konfiguration eingestellt,
z.B. alle 15 Min) – ein E-Ink-Gerät kann nicht "gepusht" werden, es fragt selbst
nach. Der Unterschied: Sobald es fragt, kriegt es garantiert den aktuellsten Stand,
nicht einen bis zu 10 Minuten alten. Wenn ihr fast in Echtzeit sehen wollt, hilft
nur ein kürzeres Poll-Intervall am Gerät – dafür würde sich Dauerstrom übers
USB-Kabel statt Akkubetrieb anbieten, da entfällt die Akku-Sorge komplett.

## Wochenansicht + Google Kalender

Das Board zeigt jetzt eine echte Wochenübersicht (Mo–So) statt nur zweier
Spalten: pro Tag der feste Block (falls vorhanden), die für diesen Tag
eingeplanten Aufgaben (mit Kürzel wer: J/K für Jonathan/Katarina), und –
falls Google Kalender verbunden ist – die Termine des Tages. Aufgaben ohne
festen Tag laufen weiter in einer Sammel-Leiste unter dem Wochenraster.

Auf der Handy-Seite bekommt jede Aufgabe jetzt ein Tag-Dropdown (beim
Eintragen und nachträglich änderbar), damit ihr wählen könnt: "diese Woche"
(kein fester Tag) oder ein bestimmter Wochentag.

### Google Kalender einmalig verbinden

Das läuft komplett lesend (read-only) und bricht nie das Board, falls es
nicht eingerichtet ist. Einrichtung — **einmalig, mit Browser, also am
PC/Laptop, nicht im Docker-Container**:

1. https://console.cloud.google.com/ → Projekt anlegen (oder bestehendes nutzen)
2. "Google Calendar API" aktivieren
3. "OAuth-Client-ID" erstellen, Anwendungstyp **Desktop-App**
4. JSON herunterladen, speichern als `var/google_credentials.json`
5. `pip install google-auth-oauthlib google-api-python-client`
6. `python scripts/setup_google_calendar.py` ausführen → Browser öffnet sich,
   einmal einloggen und Lesezugriff erlauben
7. Das erzeugt `var/google_token.json`
8. Diese Datei ins Docker-Volume kopieren (z.B.
   `docker cp var/google_token.json haushalts-board:/app/var/`) und den
   Container einmal neu starten

Danach holt sich das Board bei jedem Refresh automatisch die Termine der
laufenden Woche vom Hauptkalender (`primary`). Wollt ihr einen zweiten
gemeinsamen Kalender mit einbeziehen, könnt ihr die Kalender-IDs
kommagetrennt über die Umgebungsvariable `CALENDAR_IDS` im
`docker-compose.yml` setzen (Standard: nur `primary`).

## Wochenplaner (feste Blöcke)

Unter "Wochenplan" auf der Handy-Seite könnt ihr benannte Blöcke (Sport,
Hobby-Tag, Studium, ...) erstellen und per Ziehen auf einen anderen Wochentag
verschieben. Die Blöcke sind nicht an eine bestimmte KW gebunden – sie bleiben,
wie sie zuletzt einsortiert wurden, bis ihr sie wieder verschiebt. Doppeltippen
auf einen Block löscht ihn (mit Rückfrage).

## Deployment (auf deiner Docker-VM, 192.168.50.61)

```bash
git clone <dein-fork/dieses-repo>
cd byos_fastapi
docker compose up -d --build
```

Danach läuft der Server auf Port 4567. Passe im `docker-compose.yml` ggf. den Port
an, falls der schon belegt ist, und binde ihn wie deine anderen Container über Caddy
ein, falls du eine Subdomain willst (nicht nötig für reinen lokalen Zugriff).

Persistenz liegt im Docker-Volume `haushalt-data` (`/app/var` im Container) –
enthält sowohl die TRMNL-Gerätedatenbank (`var/db/trmnl.db`) als auch
`var/haushalt_state.json`.

## Gerät konfigurieren (BYOD/S)

Das TRMNL-Kit muss auf euren eigenen Server zeigen statt auf trmnl.com – dafür gibt es
keinen Extra-Code nötig, das ist Standard-BYOS-Verhalten der offiziellen Firmware.
Beim ersten Boot verbindet sich das Gerät mit eurem WLAN und lässt sich über die
Setup-Oberfläche auf `http://<server-ip>:4567` konfigurieren (siehe Original-README,
Abschnitt "Device Setup" für die genauen Schritte – die App-Seite unter `/` zeigt
euch außerdem den aktuellen Geräte-Status und Logs, falls etwas nicht ankommt).

## Wie die Aufgaben funktionieren

- **Neue Woche:** Jeden Montag (ISO-Kalenderwoche) entsteht automatisch eine neue
  Aufgabenliste – mit den Standardaufgaben plus allen als "wiederkehrend"
  markierten.
- **Wiederkehrend:** Nur bei Jonathan/Katarina möglich (nicht bei den
  Kinderaufgaben, die bleiben bewusst pro Woche neu).
- **Board-Bild:** Wird beim nächsten Plugin-Refresh (max. alle 10 Min) neu gerendert;
  das Gerät selbst holt sich das Bild in seinem eigenen Poll-Intervall ab (im
  Firmware-Setup einstellbar, z.B. alle 15–30 Min – kürzer kostet mehr Akku).

## Testen ohne Hardware

```bash
pip install -r requirements.txt
python -m trmnl_server.main
```

Dann `http://localhost:4567/web/haushalt.html` im Browser öffnen, um Aufgaben
einzutragen, und `http://localhost:4567/api/haushalt/state` um den aktuellen
JSON-Stand zu sehen. Das gerenderte Board-Bild landet nach dem ersten Refresh unter
`var/generated/haushalt/haushalt.png` (bzw. `.bmp` fürs Gerät).
