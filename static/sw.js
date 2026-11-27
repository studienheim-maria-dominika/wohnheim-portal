// Service worker: makes the portal installable and shows a friendly page when the phone is offline.
// It deliberately caches nothing else, so everyone always sees live data (laundry times, bookings).
const OFFLINE_HTML = `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px/1.5 system-ui,sans-serif;
background:#f5f6f8;color:#111827;text-align:center;padding:24px}@media(prefers-color-scheme:dark){body{background:#0d0f13;color:#eef0f3}}
button{margin-top:16px;padding:10px 18px;border:0;border-radius:10px;background:#c2410c;color:#fff;font:inherit;font-weight:600}</style>
</head><body><div><h1 style="font-size:20px;margin:0 0 6px">Keine Verbindung · No connection</h1>
<p style="margin:0;opacity:.7">Bitte Internet prüfen und erneut versuchen.<br>Check your internet and try again.</p>
<button onclick="location.reload()">Erneut versuchen · Retry</button></div></body></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() =>
    new Response(OFFLINE_HTML, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
