// App-Hülle offline verfügbar halten. Daten kommen immer live aus Supabase.
const CACHE = "semesterboard-v25";
const SHELL = ["./", "index.html", "config.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.hostname.endsWith("supabase.co") || url.hostname.endsWith("basemaps.cartocdn.com")) return;
  // Netzwerk zuerst, damit Updates sofort ankommen; ohne Netz aus dem Cache.
  // Eigene Dateien immer beim Server nachfragen (GitHub Pages lässt sie sonst 10 Minuten im Browser-Cache).
  const req = url.origin === self.location.origin ? new Request(e.request.url, { cache: "no-cache", credentials: "same-origin" }) : e.request;
  e.respondWith(fetch(req).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r; }).catch(() => caches.match(e.request)));
});
