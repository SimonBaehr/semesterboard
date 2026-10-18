// strava-sync: holt neue Strava-Aktivitäten sofort ins Semesterboard (Tabelle fit_workouts).
//
// POST  (mit Supabase-Login als Bearer)  -> synchronisiert die letzten Tage.
//        Ist Strava noch nicht verbunden, kommt {needsAuth:true,url} zurück.
// GET   ?code=…&state=…                   -> Rückweg von Strava nach der einmaligen Freigabe.
//
// Secrets (Supabase > Edge Functions > Secrets): STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET.
// Die Strava-Tokens liegen in public.strava_auth (RLS ohne Policies, nur Service Role).
import { createClient } from "npm:@supabase/supabase-js@2";

const APP_URL = "https://simonbaehr.github.io/semesterboard/";
const SB_URL = Deno.env.get("SUPABASE_URL")!;
const db = createClient(SB_URL, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
const CID = Deno.env.get("STRAVA_CLIENT_ID") ?? "";
const CSECRET = Deno.env.get("STRAVA_CLIENT_SECRET") ?? "";
const CALLBACK = `${SB_URL}/functions/v1/strava-sync`;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
};
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { ...CORS, "Content-Type": "application/json" } });

// state = owner.signatur, damit nur ein Link aus der eigenen App einen Token speichern kann
async function sign(v: string) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(CSECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const s = new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(v)));
  return btoa(String.fromCharCode(...s)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
const authUrl = async (owner: string) =>
  `https://www.strava.com/oauth/authorize?client_id=${CID}&response_type=code&approval_prompt=auto&scope=activity:read_all&redirect_uri=${encodeURIComponent(CALLBACK)}&state=${owner}.${await sign(owner)}`;

async function tokenCall(params: Record<string, string>) {
  const r = await fetch("https://www.strava.com/oauth/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: CID, client_secret: CSECRET, ...params }),
  });
  if (!r.ok) throw new Error("Strava-Token: " + r.status);
  return await r.json();
}

async function accessToken(owner: string): Promise<string | null> {
  const { data } = await db.from("strava_auth").select("*").eq("owner", owner).maybeSingle();
  if (!data) return null;
  if (data.expires_at - 120 > Date.now() / 1000) return data.access_token;
  const t = await tokenCall({ grant_type: "refresh_token", refresh_token: data.refresh_token });
  await db.from("strava_auth").update({ access_token: t.access_token, refresh_token: t.refresh_token, expires_at: t.expires_at, updated_at: new Date().toISOString() }).eq("owner", owner);
  return t.access_token;
}

const TYPE: Record<string, string> = {
  WeightTraining: "kraft", Crossfit: "kraft",
  Walk: "sonstiges", Hike: "sonstiges",
  Yoga: "mobility", Pilates: "mobility",
  RockClimbing: "sport", Tennis: "sport", Workout: "sport", HighIntensityIntervalTraining: "sport",
  Badminton: "sport", Squash: "sport", Soccer: "sport", TableTennis: "sport", Padel: "sport", Pickleball: "sport",
};
const RUNS = ["Run", "TrailRun", "VirtualRun"];

function toRow(a: any, owner: string) {
  const sport = a.sport_type || a.type || "Workout";
  const km = Math.round((a.distance || 0) / 10) / 100;
  const hm = Math.round(a.total_elevation_gain || 0);
  const mov = a.moving_time || a.elapsed_time || 60;
  const parts = [sport];
  if (km > 0) parts.push(`${km.toFixed(1)} km`);
  if (RUNS.includes(sport) && km > 0) { const p = mov / km; parts.push(`${Math.floor(p / 60)}:${String(Math.round(p % 60)).padStart(2, "0")} /km`); }
  if (hm > 0) parts.push(`${hm} hm`);
  parts.push("aus Strava");
  const loc = [a.location_city, a.location_country].filter(Boolean).join(", ");
  const row: Record<string, unknown> = {
    id: `strava-${a.id}`, owner,
    date: String(a.start_date_local).slice(0, 10), time: String(a.start_date_local).slice(11, 16),
    type: TYPE[sport] ?? (km > 0 ? "ausdauer" : "sport"),
    title: a.name || sport, duration_min: Math.max(1, Math.round(mov / 60)),
    details: parts.join(" · "), done: true, done_at: a.start_date,
    sport, km, moving_s: mov, hm, effort: a.suffer_score != null ? Math.round(a.suffer_score) : null,
    poly: a.map?.summary_polyline || "",
  };
  if (a.calories) row.kcal = Math.round(a.calories);
  if (loc) row.loc = loc;
  return row;
}

async function sync(owner: string) {
  const token = await accessToken(owner);
  if (!token) return { needsAuth: true, url: await authUrl(owner) };
  // ab dem letzten Strava-Eintrag minus 3 Tage (fängt nachträglich bearbeitete Aktivitäten mit ab)
  const { data: last } = await db.from("fit_workouts").select("done_at").eq("owner", owner).like("id", "strava-%").order("done_at", { ascending: false }).limit(1);
  const since = last?.[0]?.done_at ? new Date(last[0].done_at).getTime() - 3 * 864e5 : Date.now() - 14 * 864e5;
  const r = await fetch(`https://www.strava.com/api/v3/athlete/activities?after=${Math.floor(since / 1000)}&per_page=60`, { headers: { Authorization: `Bearer ${token}` } });
  if (r.status === 401) return { needsAuth: true, url: await authUrl(owner) };
  if (!r.ok) throw new Error("Strava: " + r.status);
  const acts: any[] = await r.json();
  if (!acts.length) { await db.from("strava_auth").update({ last_sync: new Date().toISOString() }).eq("owner", owner); return { added: 0, updated: 0 }; }
  const ids = acts.map((a) => `strava-${a.id}`);
  const { data: have } = await db.from("fit_workouts").select("id,kcal").in("id", ids);
  const known = new Map((have ?? []).map((h: any) => [h.id, h.kcal]));
  const rows = [];
  for (const a of acts) {
    const id = `strava-${a.id}`;
    // Kalorien gibt es nur in der Detailansicht: nur für neue Aktivitäten oder fehlende Werte nachladen
    if (!known.has(id) || known.get(id) == null) {
      const d = await fetch(`https://www.strava.com/api/v3/activities/${a.id}`, { headers: { Authorization: `Bearer ${token}` } });
      if (d.ok) Object.assign(a, { calories: (await d.json()).calories });
    }
    rows.push(toRow(a, owner));
  }
  const { error } = await db.from("fit_workouts").upsert(rows, { onConflict: "id" });
  if (error) throw error;
  await db.from("strava_auth").update({ last_sync: new Date().toISOString() }).eq("owner", owner);
  const added = rows.filter((x) => !known.has(x.id as string)).length;
  return { added, updated: rows.length - added };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (!CID || !CSECRET) return json({ error: "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET fehlen in den Supabase-Secrets." }, 500);
  try {
    const u = new URL(req.url);
    if (req.method === "GET") {
      const code = u.searchParams.get("code"), state = u.searchParams.get("state") || "";
      const [owner, sig] = state.split(".");
      if (!code || !owner || sig !== await sign(owner)) return Response.redirect(APP_URL + "?strava=fehler", 302);
      if (!(u.searchParams.get("scope") || "").includes("activity:read")) return Response.redirect(APP_URL + "?strava=rechte", 302);
      const t = await tokenCall({ grant_type: "authorization_code", code });
      await db.from("strava_auth").upsert({ owner, athlete_id: t.athlete?.id ?? null, access_token: t.access_token, refresh_token: t.refresh_token, expires_at: t.expires_at, updated_at: new Date().toISOString() });
      return Response.redirect(APP_URL + "?strava=ok", 302);
    }
    const jwt = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
    const { data: { user } } = await db.auth.getUser(jwt);
    if (!user) return json({ error: "Nicht angemeldet." }, 401);
    return json(await sync(user.id));
  } catch (e) {
    return json({ error: String((e as Error).message || e) }, 500);
  }
});
