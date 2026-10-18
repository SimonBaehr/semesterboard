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
import time
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


RUN_TYPES = ("running", "trail_running", "treadmill_running", "track_running", "virtual_run", "indoor_running")
BEST_D = [400, 1000, 1609, 5000, 10000, 16093, 21097, 42195]


def C(i):
    """Energiekosten je Meter nach Minetti 2002, i = Steigung (Höhe/Strecke)."""
    i = max(-0.45, min(0.45, i))
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def series_of(det):
    """Punktreihe aus get_activity_details. Spalten aus metricDescriptors gelesen (Werte kommen schon in s, m, m/s, bpm)."""
    idx = {d["key"]: d["metricsIndex"] for d in det.get("metricDescriptors") or []}
    need = {"t": "sumDuration", "te": "sumElapsedDuration", "d": "sumDistance", "hr": "directHeartRate",
            "v": "directSpeed", "h": "directElevation"}
    rows = []
    for m in det.get("activityDetailMetrics") or []:
        vals = m.get("metrics") or []
        r = {}
        for k, key in need.items():
            j = idx.get(key)
            r[k] = vals[j] if j is not None and j < len(vals) else None
        if r["t"] is None:
            continue
        rows.append(r)
    rows.sort(key=lambda r: r["t"])
    return rows


def analyse_activity(a, det):
    rows = series_of(det) if det else []
    out = {"n_points": len(rows)}
    if len(rows) < 2:
        return out
    # Puls: zeitgewichtet (Smart Recording, Abstände 1-10 s); Lücken > 15 s gelten als fehlend
    hist, miss, hr_pts = {}, 0.0, 0
    for r0, r1 in zip(rows, rows[1:]):
        dt_ = (r1["t"] or 0) - (r0["t"] or 0)
        if dt_ <= 0:
            continue
        hr = r0["hr"]
        if hr and 30 < hr < 240 and dt_ <= 15:
            b = int(round(hr))
            hist[b] = hist.get(b, 0) + dt_
            hr_pts += 1
        else:
            miss += dt_
    out["hr_points"] = hr_pts
    out["hr_missing_s"] = int(round(miss))
    out["hr_hist"] = {str(k): round(v, 1) for k, v in sorted(hist.items())}
    # höchster Puls, der mindestens 10 s gehalten wurde
    best10, j = None, 0
    hrrows = [r for r in rows if r["hr"] and 30 < r["hr"] < 240]
    for i in range(len(hrrows)):
        while j < len(hrrows) and hrrows[j]["t"] - hrrows[i]["t"] < 10:
            j += 1
        if j >= len(hrrows):
            break
        m = min(r["hr"] for r in hrrows[i:j + 1])
        best10 = m if best10 is None or m > best10 else best10
    out["max_hr_10s"] = best10
    typ = (a.get("activityType") or {}).get("typeKey", "")
    if typ not in RUN_TYPES:
        return out
    # Laufen: 100-m-Abschnitte für Tempo, GAP und Splits
    pts = [r for r in rows if r["d"] is not None and r["te"] is not None]
    if len(pts) < 2 or (pts[-1]["d"] or 0) < 400:
        return out
    has_h = sum(1 for r in pts if r["h"] is not None) > len(pts) * 0.8
    segs, s0 = [], pts[0]
    for r in pts[1:]:
        if r["d"] - s0["d"] >= 100:
            dd, dt_ = r["d"] - s0["d"], r["t"] - s0["t"]
            dh = (r["h"] - s0["h"]) if has_h and r["h"] is not None and s0["h"] is not None else None
            if dt_ > 0:
                segs.append({"d0": s0["d"], "dd": dd, "dt": dt_, "dh": dh})
            s0 = r
    pace_hist = {}
    for g in segs:
        pace = g["dt"] / g["dd"] * 1000
        if not 150 <= pace <= 900:
            continue
        gp = pace * C(0) / C(g["dh"] / g["dd"]) if g["dh"] is not None else pace
        b = int(gp // 5 * 5)
        pace_hist[str(b)] = round(pace_hist.get(str(b), 0) + g["dd"], 1)
    out["pace_hist"] = pace_hist
    out["gap"] = has_h
    # Bestleistungen: gleitendes Fenster über die Distanzreihe, verstrichene Zeit
    best = {}
    for D in BEST_D:
        if pts[-1]["d"] - pts[0]["d"] < D:
            continue
        i, bt = 0, None
        for k in range(len(pts)):
            while i + 1 < k and pts[k]["d"] - pts[i + 1]["d"] >= D:
                i += 1
            span = pts[k]["d"] - pts[i]["d"]
            if span >= D:
                t = (pts[k]["te"] - pts[i]["te"]) * D / span
                if t > 0 and D / t < 7.0 and (bt is None or t < bt):
                    bt = t
        if bt:
            best[str(D)] = round(bt, 1)
    out["best"] = best
    # Splits je km mit GAP
    splits, km, acc = [], 1, {"t": 0, "gt": 0, "dh": 0, "hrw": 0, "hrt": 0}
    hr_at = lambda d: next((r["hr"] for r in pts if r["d"] >= d and r["hr"]), None)
    for g in segs:
        acc["t"] += g["dt"]
        acc["gt"] += g["dt"] * C(0) / C(g["dh"] / g["dd"]) if g["dh"] is not None else g["dt"]
        acc["dh"] += g["dh"] or 0
        hr = hr_at(g["d0"])
        if hr:
            acc["hrw"] += hr * g["dt"]
            acc["hrt"] += g["dt"]
        if g["d0"] + g["dd"] >= km * 1000:
            splits.append([km, round(acc["t"]), round(acc["gt"]) if has_h else None, round(acc["hrw"] / acc["hrt"]) if acc["hrt"] else None, round(acc["dh"])])
            km += 1
            acc = {"t": 0, "gt": 0, "dh": 0, "hrw": 0, "hrt": 0}
    out["splits"] = splits
    # Kurve für die Zeichnung: höchstens 240 Punkte [Zeit s, Puls, Tempo s/km], 30 s geglättet
    step = max(1, len(pts) // 240)
    ser = []
    for k in range(0, len(pts), step):
        w = [r for r in pts[max(0, k - step * 2):k + step * 2 + 1]]
        dd = w[-1]["d"] - w[0]["d"]
        dt_ = w[-1]["t"] - w[0]["t"]
        hrs = [r["hr"] for r in w if r["hr"]]
        ser.append([round(pts[k]["t"]), round(sum(hrs) / len(hrs)) if hrs else None, round(dt_ / dd * 1000) if dd > 0 and dt_ > 0 and dt_ / dd * 1000 < 1200 else None])
    out["series"] = ser
    return out


def sport_of(a):
    t = (a.get("activityType") or {}).get("typeKey", "")
    if t in RUN_TYPES:
        return "run"
    if "cycling" in t or "biking" in t or "ride" in t:
        return "ride"
    if t in ("hiking", "walking", "mountaineering"):
        return "hike"
    if "strength" in t or "fitness" in t or "hiit" in t or "cardio" in t:
        return "strength"
    return "other"


def run(g, days):
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    acts = safe(g.get_activities_by_date, start.isoformat(), end.isoformat()) or []
    log(f"{len(acts)} Einheiten im Zeitraum.")
    rows, recent_runs = [], 0
    for n, a in enumerate(sorted(acts, key=lambda x: x.get("startTimeLocal") or "", reverse=True)):
        aid = a.get("activityId")
        dur = int(a.get("duration") or 0)
        has_hr = bool(a.get("averageHR"))
        det = safe(g.get_activity_details, aid, max(2000, dur + 60), 0) if (has_hr or sport_of(a) == "run") and dur > 0 else None
        res = analyse_activity(a, det)
        if det is not None:
            time.sleep(0.4)  # Garmin nicht mit Abrufen fluten
        sp = sport_of(a)
        keep_series = sp == "run" and recent_runs < 10
        if sp == "run":
            recent_runs += 1
        rows.append({
            "id": f"g-{aid}", "date": (a.get("startTimeLocal") or "")[:10], "start_local": a.get("startTimeLocal"),
            "sport": sp, "name": (a.get("activityName") or "")[:120],
            "dur_s": int(a.get("elapsedDuration") or dur), "moving_s": int(a.get("movingDuration") or dur),
            "dist_m": a.get("distance"), "elev_m": a.get("elevationGain"),
            "avg_hr": a.get("averageHR"), "max_hr": a.get("maxHR"), "device_load": a.get("activityTrainingLoad"),
            "trimp": None, "n_points": res.get("n_points"), "hr_points": res.get("hr_points"), "hr_missing_s": res.get("hr_missing_s"),
            "zone_s": {"hist": res.get("hr_hist"), "max10": res.get("max_hr_10s"),
                       "garmin": [a.get(f"hrTimeInZone_{z}") for z in range(1, 6)]},
            "pace_km": {"hist": res.get("pace_hist"), "gap": res.get("gap")} if res.get("pace_hist") else None,
            "best": res.get("best") or None,
            "series": res.get("series") if keep_series else None,
            "splits": res.get("splits") if keep_series else None,
        })
        if n % 25 == 0:
            log(f"{n + 1}/{len(acts)} gerechnet")
    log(f"{len(rows)} Einheiten, {sum(1 for r in rows if (r['hr_points'] or 0) > 0)} mit Pulsreihe, "
        f"{sum(1 for r in rows if r['pace_km'])} mit Tempo-/Distanzreihe.")
    post("fit_analysis", rows)
    # Profil, Schwelle, Rekorde, Vorhersagen des Geräts
    prof = (safe(g.get_user_profile) or {}).get("userData") or {}
    bd = prof.get("birthDate")
    age = None
    if bd:
        b = dt.date.fromisoformat(bd[:10])
        age = end.year - b.year - ((end.month, end.day) < (b.month, b.day))
    zones = None
    for a in acts:
        if a.get("averageHR"):
            z = safe(g.get_activity_hr_in_timezones, a.get("activityId"))
            if z:
                zones = [x.get("zoneLowBoundary") for x in z]
                break
    recs = {}
    for r in safe(g.get_personal_record) or []:
        if r.get("typeId") in (1, 2, 3, 4, 5, 6) and r.get("activityType") in RUN_TYPES:
            recs[{1: "1000", 2: "1609", 3: "5000", 4: "10000", 5: "21097", 6: "42195"}[r["typeId"]]] = {
                "s": r.get("value"), "date": (r.get("activityStartDateTimeLocalFormatted") or r.get("prStartTimeGmtFormatted") or "")[:10],
                "name": r.get("activityName")}
    rp = safe(g.get_race_predictions) or {}
    mm = safe(g.get_max_metrics, end.isoformat()) or []
    vo2 = None
    if isinstance(mm, list) and mm:
        vo2 = ((mm[0] or {}).get("generic") or {}).get("vo2MaxPreciseValue")
    lts = prof.get("lactateThresholdSpeed")
    post("fit_coach", [{"id": "profile", "data": {
        "gender": prof.get("gender"), "age": age, "vo2max": vo2 or prof.get("vo2MaxRunning"),
        "lthr": prof.get("lactateThresholdHeartRate"), "lthr_auto": prof.get("thresholdHeartRateAutoDetected"),
        "lt_speed_raw": lts, "lt_speed_mps": round(lts * 10, 3) if lts else None,
        "profile_maxhr": prof.get("maxHeartRate"), "garmin_zone_low": zones,
        "records": recs, "race_pred": {k: rp.get(k) for k in ("time5K", "time10K", "timeHalfMarathon", "timeMarathon")} if isinstance(rp, dict) else None,
        "window_days": days, "computed": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }}])
    log("Fertig.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--days", type=int, default=21)
    a = ap.parse_args()
    g = client()
    if a.inspect:
        return inspect(g)
    run(g, max(1, min(a.days, 400)))


if __name__ == "__main__":
    main()
