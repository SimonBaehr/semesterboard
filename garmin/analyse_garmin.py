#!/usr/bin/env python3
"""
Garmin-Trainingsanalyse fürs Semesterboard.

Rechnet aus den Rohdaten der Uhr (Punktreihen je Einheit) Belastung, Zonen, Bestleistungen,
Fitness/Ermüdung/Frische und einen Coach-Befund und schreibt die Ergebnisse über die
Supabase-Funktion garmin-ingest in die Tabellen fit_analysis und fit_coach.
Nur lesen bei Garmin. Keine Gesundheitswerte ins (öffentliche) GitHub-Log.

  python analyse_garmin.py --inspect          Rohblöcke einer Einheit in garmin_debug ablegen (Schritt 3)
  python analyse_garmin.py --days 365         volle Analyse über 12 Monate
  python analyse_garmin.py --days 21          nur neue/letzte Einheiten nachrechnen, Rest bleibt
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_garmin import client, safe, INGEST_URL  # noqa: E402

QUIET = bool(os.environ.get("CI"))


def log(*a):
    print(*a, flush=True)


def post(table, rows):
    secret = os.environ.get("GARMIN_INGEST_SECRET", "")
    if len(secret) < 16:
        sys.exit("GARMIN_INGEST_SECRET fehlt.")
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        req = urllib.request.Request(os.environ.get("GARMIN_INGEST_URL", INGEST_URL),
                                     data=json.dumps({"table": table, "rows": chunk}, default=str).encode(),
                                     method="POST", headers={"Content-Type": "application/json", "x-ingest-secret": secret})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                log(table, r.read().decode()[:120])
        except urllib.error.HTTPError as e:
            sys.exit(f"Senden an {table} fehlgeschlagen: HTTP {e.code} {e.read().decode()[:300]}")


def shrink(o, depth=0):
    """Rohblock kürzen, damit er in die Datenbank passt (lange Listen auf 30 Einträge)."""
    if isinstance(o, dict):
        return {k: shrink(v, depth + 1) for k, v in o.items()}
    if isinstance(o, list):
        return [shrink(v, depth + 1) for v in o[:30]] + ([f"... {len(o) - 30} weitere"] if len(o) > 30 else [])
    return o


def inspect(g):
    acts = safe(g.get_activities, 0, 10) or []
    run = next((a for a in acts if "run" in str((a.get("activityType") or {}).get("typeKey", ""))), acts[0] if acts else None)
    out = {"activities_count_sample": len(acts)}
    if run:
        aid = run.get("activityId")
        dur = int(run.get("duration") or 0)
        det = safe(g.get_activity_details, aid, max(2000, dur + 10), 4000) or {}
        out.update({
            "list_raw": run,
            "details_keys": list(det.keys()) if isinstance(det, dict) else str(type(det)),
            "details_descriptors": det.get("metricDescriptors") if isinstance(det, dict) else None,
            "details_n_points": len(det.get("activityDetailMetrics") or []) if isinstance(det, dict) else None,
            "details_first30": (det.get("activityDetailMetrics") or [])[:30] if isinstance(det, dict) else None,
            "details_other": shrink({k: v for k, v in det.items() if k not in ("activityDetailMetrics", "metricDescriptors", "geoPolylineDTO")}) if isinstance(det, dict) else None,
            "splits": shrink(safe(g.get_activity_splits, aid)),
            "hr_zones": safe(g.get_activity_hr_in_timezones, aid),
        })
    prof = safe(g.get_user_profile) or {}
    ud = prof.get("userData") if isinstance(prof, dict) else {}
    keep = lambda k: any(s in k.lower() for s in ("heart", "hr", "zone", "lactate", "thresh", "birth", "gender", "weight", "height", "vo2", "max"))
    out["profile_fields"] = {k: v for k, v in (ud or {}).items() if keep(k)}
    out["profile_top_keys"] = list(prof.keys()) if isinstance(prof, dict) else None
    out["lactate"] = safe(g.get_lactate_threshold, True) if hasattr(g, "get_lactate_threshold") else "fehlt"
    out["records"] = safe(g.get_personal_record)
    out["race_pred"] = safe(g.get_race_predictions)
    out["max_metrics"] = safe(g.get_max_metrics, dt.date.today().isoformat())
    out["methods"] = sorted(m for m in dir(g) if m.startswith("get_") and any(s in m for s in ("activit", "zone", "lactate", "record", "race", "max", "rhr", "heart", "profile", "settings", "split")))
    post("garmin_debug", [{"id": "inspect", "data": out}])
    log("Rohblöcke in garmin_debug abgelegt.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--days", type=int, default=21)
    a = ap.parse_args()
    g = client()
    if a.inspect:
        return inspect(g)
    sys.exit("Analyse folgt im nächsten Schritt.")


if __name__ == "__main__":
    main()
