// mensa: liest den Speiseplan der Mensa Pasing vom Studierendenwerk München Oberbayern
// und gibt ihn als JSON zurück. Öffentliche Daten, daher ohne Login; Antwort 3 h im Cache.
// GET -> {source, days:[{date:"YYYY-MM-DD", dishes:[{cat,title,veg,typ}]}]}
//   veg: 0 = mit Fleisch/Fisch, 1 = vegetarisch, 2 = vegan (Feld data-essen-fleischlos der Seite)
const URL_PLAN = "https://www.studierendenwerk-muenchen-oberbayern.de/gastronomie/speiseplan/speiseplan_432_-de.html";
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info" };

const unescape = (s: string) =>
  s.replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#039;|&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();

function parse(html: string) {
  const days = [];
  for (const block of html.split('class="c-schedule__item"').slice(1)) {
    const d = block.match(/heute_(\d{4}-\d{2}-\d{2})/);
    if (!d) continue;
    const dishes = [];
    for (const li of block.split("c-menu-dish-list__item ").slice(1)) {
      const title = li.match(/c-menu-dish__title">([^<]+)</);
      if (!title) continue;
      const cat = li.match(/stwm-artname">([^<]+)</);
      const veg = li.match(/data-essen-fleischlos="(\d)"/);
      const typ = li.match(/data-essen-typ="([^"]*)"/);
      dishes.push({ cat: cat ? unescape(cat[1]) : "", title: unescape(title[1]), veg: veg ? +veg[1] : 0, typ: typ ? typ[1] : "" });
    }
    days.push({ date: d[1], dishes });
  }
  return days;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const r = await fetch(URL_PLAN, { headers: { "User-Agent": "Semesterboard (privat)" } });
    if (!r.ok) throw new Error("Studierendenwerk: " + r.status);
    const days = parse(await r.text());
    return new Response(JSON.stringify({ source: URL_PLAN, days }), {
      headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "public, max-age=10800" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: String((e as Error).message || e) }), { status: 502, headers: { ...CORS, "Content-Type": "application/json" } });
  }
});
