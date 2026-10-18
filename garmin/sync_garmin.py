#!/usr/bin/env python3
"""
Garmin -> Semesterboard (Supabase)

Holt Simons Erholungswerte aus Garmin Connect (Schlaf, HRV, Ruhepuls, Body Battery,
Stress, Schritte, Training Readiness) und schickt sie an die Supabase Edge Function
"garmin-ingest". Nur lesen: Das Script schreibt nie etwas zu Garmin zurück.

Basis: python-garminconnect von cyberjunky (inoffizieller Login, Garmin hat keine
öffentliche API). https://github.com/cyberjunky/python-garminconnect

Verwendung
  python sync_garmin.py --login              einmalig am eigenen Rechner, fragt E-Mail, Passwort (verdeckt) und ggf. 2FA
  python sync_garmin.py --export-ci-token    schreibt garmin-ci-token.txt für das GitHub-Secret GARMIN_TOKEN_B64
  python sync_garmin.py --days 3 --dry-run   testet und zeigt die Werte, schickt nichts
  python sync_garmin.py --days 3             holt die letzten 3 Tage und schickt sie an Supabase

Umgebungsvariablen beim Senden
  GARMIN_TOKEN_B64       Token-Bündel (nur in GitHub Actions, sonst wird der lokale Token genutzt)
  GARMIN_INGEST_SECRET   gemeinsamer Schlüssel, identisch mit dem Edge-Function-Secret in Supabase
  GARMIN_INGEST_URL      optional, Standard: Simons garmin-ingest-Funktion

Das Passwort wird nie gespeichert, nie ausgegeben und landet nie in einer Umgebungsvariable.
Der Token wird nie auf dem Bildschirm ausgegeben.
"""
import argparse
import datetime as dt
import getpass
import json
import os
import stat
import sys
import urllib.request
from pathlib import Path

INGEST_URL = "https://aisdyuwigbthyshhutmd.supabase.co/functions/v1/garmin-ingest"
TOKEN_DIR = Path.home() / ".garminconnect"


def need_lib():
    try:
        from garminconnect import Garmin  # noqa: F401
    except ImportError:
        sys.exit("Bitte zuerst installieren:  pip install -r requirements.txt")


TOKEN_FILE = "garmin_tokens.json"  # ab garminconnect 0.3: Tokens liegen in <ordner>/garmin_tokens.json


def login_interactive():
    need_lib()
    from garminconnect import Garmin
    if not sys.stdin.isatty():
        sys.exit("Bitte in einem echten Terminal ausführen, damit das Passwort verdeckt eingegeben werden kann.")
    email = input("Garmin-E-Mail: ").strip()
    pw = getpass.getpass("Garmin-Passwort (unsichtbar): ")
    TOKEN_DIR.mkdir(mode=0o700, exist_ok=True)
    g = Garmin(email=email, password=pw, prompt_mfa=lambda: input("2FA-Code von Garmin: ").strip())
    try:
        g.login(str(TOKEN_DIR))  # speichert die Tokens automatisch in TOKEN_DIR
    finally:
        pw = None  # noqa: F841
    if not (TOKEN_DIR / TOKEN_FILE).exists():
        sys.exit("Login lief durch, aber es wurde keine Token-Datei gespeichert. Bitte Screenshot an Claude schicken.")
    print("Login ok. Token gespeichert (privat).")


def client():
    need_lib()
    from garminconnect import Garmin
    b64 = os.environ.get("GARMIN_TOKEN_B64", "").strip()
    store = TOKEN_DIR
    if b64:  # GitHub Actions: Token-Datei aus dem Secret in einen privaten Temp-Ordner schreiben
        import base64
        import tempfile
        store = Path(tempfile.mkdtemp(prefix="garmin-"))
        (store / TOKEN_FILE).write_bytes(base64.b64decode(b64))
    elif not (TOKEN_DIR / TOKEN_FILE).exists():
        sys.exit("Kein Token gefunden. Erst einmal:  python sync_garmin.py --login")
    g = Garmin()
    try:
        g.login(str(store))
    except Exception as e:  # Token abgelaufen oder Garmin hat den Login geändert
        sys.exit(f"Garmin-Login mit Token fehlgeschlagen ({type(e).__name__}). "
                 "Lösung: pip install -U garminconnect, dann --login und --export-ci-token neu ausführen.")
    return g


def export_ci_token():
    import base64
    f = TOKEN_DIR / TOKEN_FILE
    if not f.exists():
        sys.exit("Kein Token gefunden. Erst einmal:  python sync_garmin.py --login")
    out = Path("garmin-ci-token.txt")
    out.write_text(base64.b64encode(f.read_bytes()).decode())
    try:
        out.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print(f"Token in {out.resolve()} geschrieben. Inhalt als GitHub-Secret GARMIN_TOKEN_B64 eintragen, danach die Datei löschen.")


def safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def pick(d, *path):
    for p in path:
        if d is None:
            return None
        d = d.get(p) if isinstance(d, dict) else (d[p] if isinstance(d, list) and isinstance(p, int) and len(d) > p else None)
    return d


def hours(sec):
    return round(sec / 3600, 2) if isinstance(sec, (int, float)) and sec > 0 else None


def day(g, d):
    ds = d.isoformat()
    stats = safe(g.get_stats, ds) or {}
    sleep = safe(g.get_sleep_data, ds) or {}
    hrv = safe(g.get_hrv_data, ds) or {}
    tr = safe(g.get_training_readiness, ds)
    s = sleep.get("dailySleepDTO") or {}
    ready = None
    if isinstance(tr, list) and tr:
        ready = max((x.get("score") for x in tr if isinstance(x, dict) and x.get("score") is not None), default=None)
    elif isinstance(tr, dict):
        ready = tr.get("score")
    return {
        "date": ds,
        "rhr": stats.get("restingHeartRate") or sleep.get("restingHeartRate"),
        "hrv": pick(hrv, "hrvSummary", "lastNightAvg"),
        "hrv_week": pick(hrv, "hrvSummary", "weeklyAvg"),
        "hrv_status": pick(hrv, "hrvSummary", "status"),
        "sleep_h": hours(s.get("sleepTimeSeconds")),
        "sleep_score": pick(s, "sleepScores", "overall", "value"),
        "deep_h": hours(s.get("deepSleepSeconds")),
        "rem_h": hours(s.get("remSleepSeconds")),
        "bb_min": stats.get("bodyBatteryLowestValue"),
        "bb_max": stats.get("bodyBatteryHighestValue"),
        "stress": stats.get("averageStressLevel") if (stats.get("averageStressLevel") or -1) >= 0 else None,
        "steps": stats.get("totalSteps"),
        "readiness": ready,
    }


def weights(g, start, end):
    """Gewicht, Körperfett und BMI je Tag (Garmin-Waage oder manuell in Garmin Connect eingetragen)."""
    data = safe(g.get_body_composition, start.isoformat(), end.isoformat()) or {}
    out = {}
    for w in data.get("dateWeightList") or []:
        d = w.get("calendarDate") or (str(w.get("date"))[:10] if w.get("date") else None)
        kg = w.get("weight")
        if not d or not isinstance(kg, (int, float)):
            continue
        kg = kg / 1000 if kg > 1000 else kg  # Garmin liefert Gramm
        out[d] = {"weight_kg": round(kg, 2), "body_fat": w.get("bodyFat"), "bmi": w.get("bmi")}
    return out


def send(rows):
    secret = os.environ.get("GARMIN_INGEST_SECRET", "")
    if len(secret) < 16:
        sys.exit("GARMIN_INGEST_SECRET fehlt oder ist zu kurz.")
    url = os.environ.get("GARMIN_INGEST_URL", INGEST_URL)
    req = urllib.request.Request(url, data=json.dumps({"wellness": rows}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-ingest-secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Supabase:", r.read().decode()[:200])  # nur {"saved": n}
    except urllib.error.HTTPError as e:
        sys.exit(f"Senden fehlgeschlagen: HTTP {e.code} {e.read().decode()[:200]}")


def main():
    ap = argparse.ArgumentParser(description="Garmin-Erholungswerte ins Semesterboard")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--export-ci-token", action="store_true")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.login:
        return login_interactive()
    if a.export_ci_token:
        return export_ci_token()
    g = client()
    today = dt.date.today()
    n = max(1, min(a.days, 370))
    rows = [day(g, today - dt.timedelta(days=i)) for i in range(n)]
    wmap = weights(g, today - dt.timedelta(days=n - 1), today)
    for r in rows:
        r.update(wmap.get(r["date"], {}))
    rows = [r for r in rows if any(v is not None for k, v in r.items() if k != "date")]
    quiet = bool(os.environ.get("CI"))  # in GitHub Actions keine Gesundheitswerte ins (öffentliche) Log schreiben
    if quiet:
        print(f"{len(rows)} Tage mit Werten gefunden.")
    for r in ([] if quiet else sorted(rows, key=lambda x: x["date"])):
        print(f'{r["date"]}: Ruhepuls {r["rhr"]} · HRV {r["hrv"]} · Schlaf {r["sleep_h"]} h (Score {r["sleep_score"]}) · '
              f'Body Battery {r["bb_min"]}->{r["bb_max"]} · Readiness {r["readiness"]} · Gewicht {r.get("weight_kg")}')
    if not rows:
        print("Keine Werte gefunden (Uhr nachts getragen?).")
        return
    if a.dry_run:
        print("Testlauf, nichts gesendet.")
        return
    send(rows)


if __name__ == "__main__":
    main()
