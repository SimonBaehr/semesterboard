# Semesterboard ABI

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
