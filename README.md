# Semesterboard ABI

Persönliche Kopie von Simon (HM, M.Sc. Applied Business Innovation, WiSe 2026/27). App: https://simonbaehr.github.io/semesterboard/

Persönlicher Master-Leitstand (Vorlesungen, Aufgaben, Modularbeits-Pläne, Notizen, Mail-Briefing) als installierbare Web-App (PWA). Über den Umschalter oben gibt es zusätzlich den Bereich **Alltag** (Training, Ernährung, Gewicht), dessen Daten in eigenen Tabellen (`fit_*`) getrennt von den Uni-Daten liegen.

- **Frontend:** eine statische Seite (`index.html`), gehostet über GitHub Pages, auf dem Handy über „Zum Startbildschirm hinzufügen“ installierbar.
- **Daten:** Supabase (Postgres + Realtime). Jede Zeile gehört dem angemeldeten Konto (Row Level Security), Änderungen erscheinen live auf allen Geräten.
- **Claude** schreibt über den Supabase-Connector direkt in dieselben Tabellen; die Morgen-Routine legt dort täglich das Mail-Briefing ab.

## Dateien

| Datei | Zweck |
|---|---|
| `index.html` | die komplette App |
| `config.js` | Supabase-URL und öffentlicher (publishable) Schlüssel |
| `manifest.webmanifest`, `sw.js`, `icons/` | Installation als App, Offline-Hülle |
| `supabase/schema.sql` | Tabellen, Zugriffsschutz, Live-Updates (Uni) |
| `supabase/alltag.sql` | eigene Tabellen für den Bereich Alltag (Training, Mahlzeiten, Gewicht, Ziele) |

## Android-App

`android/` enthält eine schlanke Android-Hülle, die die Web-App lädt. Bei jeder Änderung in `android/` baut GitHub Actions eine neue APK und hängt sie an ein Release. Neueste Version: https://github.com/SimonBaehr/semesterboard/releases/latest/download/semesterboard.apk

## Ergänzungen in dieser Kopie

- **Anfahrt:** Im Cockpit steht für den nächsten Präsenztermin am Campus Pasing die S8 ab Johanneskirchen, mit der man mindestens 20 Minuten vor Beginn da ist (28 min Fahrt, ca. 15 min Fußweg). Wenn möglich mit MVG-Fahrplandaten, sonst aus dem 20-Minuten-Takt berechnet.
- **Laufvorschlag:** Eine tägliche Routine liest die Strava-Daten und legt um ca. 6 Uhr einen Eintrag „Laufvorschlag: …“ in `fit_workouts` an, der im Bereich Fitness oben erscheint.
- Kein Mail-Briefing.

## Garmin-Erholungswerte (optional)

`garmin/sync_garmin.py` holt Schlaf, HRV, Ruhepuls, Body Battery, Stress und Training Readiness
(über die inoffizielle Bibliothek [python-garminconnect](https://github.com/cyberjunky/python-garminconnect))
und schickt sie an die Supabase Edge Function `garmin-ingest`, die sie in `fit_wellness` schreibt.
Der Workflow `.github/workflows/garmin-sync.yml` läuft jeden Morgen. Benötigte GitHub-Secrets:
`GARMIN_TOKEN_B64` (aus `--export-ci-token`) und `GARMIN_INGEST_SECRET` (identisch mit dem
Edge-Function-Secret gleichen Namens in Supabase). Werte werden im Log nicht ausgegeben.
