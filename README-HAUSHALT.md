# Haushalts-Board – Self-Hosted TRMNL Setup

Dieses Repo ist ein Fork von [usetrmnl/byos_fastapi](https://github.com/usetrmnl/byos_fastapi),
erweitert um ein eigenes Plugin (`Haushalts-Board`) für den Kühlschrank-Bildschirm
(TRMNL 7.5" OG DIY Kit, 800×480). Die Basis-README (`README.md`) vom Original-Projekt
gilt weiterhin für alles Generelle (Setup, andere Plugins, Firmware); hier steht nur,
was für unser Vorhaben speziell dazugekommen ist.

## Was wurde hinzugefügt

- `trmnl_server/haushalt_store.py` – JSON-Datei-Speicher (`var/haushalt_state.json`)
  für alle Aufgaben. Eine flache Aufgabenliste mit **echtem Datum**, optionaler
  **Uhrzeit**, Besitzer (Jonathan / Katarina / Kinder / Alle), `hervorheben`-Flag
  und wöchentlichen Wiederholungs-Vorlagen.
- `trmnl_server/plugins/haushalt.py` – Plugin, das das Board als 800×480-Bild
  rendert: rollierende 3-Tage-Ansicht (Heute / Morgen / Übermorgen) plus eine
  Leiste "Diese Woche" für Aufgaben ohne festen Tag.
- `trmnl_server/routes/haushalt.py` – JSON-API unter `/api/haushalt/...` zum
  Anlegen, Bearbeiten, Abhaken, Umsortieren und Löschen von Aufgaben.
- `web/haushalt.html` – Handy-taugliche Tagesagenda: Aufgaben nach Tag gruppiert,
  Eingabe über ein Bottom-Sheet mit Schnell-Chips (Heute / Morgen / Wochentage)
  und Kalender-Datepicker für alles Weitere. Drag & Drop läuft über
  Pointer-Events, nicht über natives HTML5-Drag&Drop – letzteres greift auf
  Smartphones nicht.
- `Dockerfile`, `docker-compose.yml` – Deployment-Setup.

## Datenmodell (Schema v2)

Eine Aufgabe hat: Text, Besitzer, optionales **Datum**, optionale **Uhrzeit**,
`erledigt`, `hervorheben` und eine Sortierposition.

- **Kein Datum** = "diese Woche, kein fester Tag" – landet in der Fußleiste des
  Boards und verfällt am Ende der Woche.
- **Hervorheben** ersetzt die früheren "festen Blöcke": die Aufgabe wird auf dem
  Board als schwarzer Balken gezeichnet, gehört aber – anders als ein Block –
  einer Person und kann eine Uhrzeit haben.
- **Wöchentliche Wiederholung** ist eine Vorlage, die an einem Wochentag hängt.
  Konkrete Vorkommen werden 21 Tage im Voraus erzeugt, damit jedes Vorkommen
  einen eigenen Erledigt-Status hat und einzeln geändert oder gelöscht werden
  kann, ohne die Reihe zu beeinflussen.

### Warum v2: der Wochentag-Bug

v1 speicherte statt eines Datums einen **Wochentag-Index 0–6 relativ zum
Wochen-Bucket**. Die Oberfläche zeigte nur "Mo/Di/Mi…" ohne Wochenbezug, was
regelmäßig das falsche echte Datum ergab: An einem Sonntag bedeutete "Mo"
*letzten* Montag (6 Tage rückwärts), und sobald die angezeigten 3 Tage über die
ISO-Wochengrenze reichten, lag die Aufgabe in einem anderen Bucket – sie
verschwand komplett vom Board (weder in einer Tagesspalte noch in "Diese Woche").

Beim ersten Start migriert der Store automatisch nach v2. Die alte Datei wird
vorher als `var/haushalt_state.v1-backup.json` gesichert. Alte Blöcke werden zu
hervorgehobenen Wochen-Wiederholungen mit Besitzer "Alle"; v1-Aufgaben, deren
Wochentag in der Vergangenheit lag, werden zu Aufgaben ohne festen Tag statt auf
einem bereits vergangenen Datum wieder aufzutauchen.

## Sortierung auf dem Board

Pro Tag werden Haushaltsaufgaben und Google-Kalender-Termine in **eine** Liste
gemischt und wie in einer Kalender-App geordnet: erst alles ohne Uhrzeit (in der
per Drag & Drop gesetzten Reihenfolge), danach alles mit Uhrzeit chronologisch.
Deshalb haben auf der Handy-Seite auch nur Einträge ohne Uhrzeit einen Anfasser
zum Verschieben – Einträge mit Uhrzeit ordnen sich selbst ein.

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

## 3-Tage-Ansicht + Google Kalender

Das Board zeigt Heute / Morgen / Übermorgen und schiebt sich jede Nacht einen
Tag weiter. Drei breite Spalten lesen sich auf dem kleinen 800×480-Panel deutlich
besser als sieben gequetschte. Pro Tag stehen dort die eingeplanten Aufgaben
(mit Kürzel wer: J/K/k/A) und – falls Google Kalender verbunden ist – die Termine
des Tages, gemeinsam nach Uhrzeit sortiert. Aufgaben ohne festen Tag laufen in
der Leiste "Diese Woche" darunter.

Die Handy-Seite plant weiter voraus als das Board: sie zeigt 14 Tage, gruppiert
nach "Diese Woche" / "Nächste Woche", sodass ihr sonntags die kommende Woche
durchplanen könnt. Die ersten drei Tage sind mit **Board** markiert – das ist
genau das, was am Kühlschrank hängt.

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

## Bedienung auf dem Handy

Der große Knopf unten rechts (oder das **+** an einem Tag) öffnet ein
Bottom-Sheet:

- **Wer?** Jonathan / Katarina / Kinder / Alle
- **Wann?** Schnell-Chips für Heute, Morgen und die nächsten Wochentage, dazu
  "Ohne festen Tag" und ein Kalender-Datepicker für alles weiter Entfernte
- **Uhrzeit** optional – setzt den Eintrag chronologisch aufs Board
- **Hervorheben** – schwarzer Balken auf dem Board
- **Jede Woche wiederholen** – braucht einen festen Tag, wiederholt sich dann an
  dessen Wochentag

Tippen auf eine Aufgabe öffnet dasselbe Sheet zum Bearbeiten (inkl. Löschen und
"Wiederholung beenden"). Die Checkbox hakt ab, der Anfasser rechts sortiert per
Ziehen um (funktioniert per Touch). Über die Filterleiste oben lässt sich auf
eine einzelne Person einschränken.

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

- **Datierte Aufgaben** bleiben an ihrem Datum stehen; länger vergangene werden
  nach zwei Wochen automatisch aufgeräumt.
- **Ohne festen Tag** gilt für die laufende Woche und verfällt beim Wochenwechsel
  – das ist die "muss diese Woche noch irgendwann passieren"-Liste.
- **Wöchentliche Wiederholung** erzeugt Vorkommen 21 Tage im Voraus. Ein einzelnes
  Vorkommen lässt sich löschen, ohne die Reihe zu beenden; "Wiederholung beenden"
  entfernt die Regel samt aller künftigen Vorkommen, vergangene bleiben stehen.
- **Board-Bild:** Wird nach jeder Änderung sofort neu gerendert (siehe oben);
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
