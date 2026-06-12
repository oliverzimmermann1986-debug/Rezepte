// Service Worker für Scrapper-PWA
// Strategy:
// - cache-first für /static/* (CSS, JS, Alpine)
// - cache-first stale-while-revalidate für /api/recipes/{id}/thumb
//   (Bilder ändern sich selten — Liste lädt instant aus dem Cache)
// - network-first für alles andere
// - Offline: Fallback auf cached '/'
const CACHE_NAME = 'scrapper-v15';
const THUMB_CACHE = 'scrapper-thumbs-v1';
const DETAIL_CACHE = 'scrapper-detail-v1';   // /api/recipes/{id} responses
const VIDEO_CACHE = 'scrapper-videos-v1';    // /api/recipes/{id}/video
const STATIC_CACHE_URLS = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/alpine.min.js',
  '/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_CACHE_URLS).catch(() => null))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names
        .filter(n => n !== CACHE_NAME && n !== THUMB_CACHE
                  && n !== DETAIL_CACHE && n !== VIDEO_CACHE)
        .map(n => caches.delete(n))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);

  // Thumbnails: cache-first stale-while-revalidate. Erster Load aus Network,
  // alle weiteren instant aus Cache. Background-Fetch updated im Hintergrund.
  if (url.pathname.match(/^\/api\/recipes\/\d+\/thumb$/)) {
    event.respondWith(
      caches.open(THUMB_CACHE).then(async (cache) => {
        const cached = await cache.match(event.request);
        const networkPromise = fetch(event.request).then(resp => {
          if (resp.ok) cache.put(event.request, resp.clone());
          return resp;
        }).catch(() => cached);
        return cached || networkPromise;
      })
    );
    return;
  }

  // Detail-API /api/recipes/{id} — cache-first für Offline-Fähigkeit.
  // Wenn online: network bevorzugt (frische Daten), Network-Result wird
  // gecached. Wenn offline: Cache-Fallback.
  // Limit: nur letzte 50 Rezepte cachen (Cleanup wenn voll).
  if (url.pathname.match(/^\/api\/recipes\/\d+$/)) {
    event.respondWith(
      caches.open(DETAIL_CACHE).then(async (cache) => {
        try {
          const resp = await fetch(event.request);
          if (resp.ok) {
            cache.put(event.request, resp.clone());
            // Async cleanup: keep last 50
            cache.keys().then(keys => {
              if (keys.length > 50) {
                keys.slice(0, keys.length - 50).forEach(k => cache.delete(k));
              }
            });
          }
          return resp;
        } catch (_) {
          const cached = await cache.match(event.request);
          return cached || new Response(JSON.stringify({error: 'offline'}),
            { status: 503, headers: {'Content-Type':'application/json'}});
        }
      })
    );
    return;
  }

  // Videos: cache-first, da groß (10-50MB pro Stück) und ändern sich nie.
  // Limit: 20 zuletzt geöffnete Videos. So passt offline-Cache in
  // vernünftige ~500MB Browser-Quota.
  if (url.pathname.match(/^\/api\/recipes\/\d+\/video$/)) {
    event.respondWith(
      caches.open(VIDEO_CACHE).then(async (cache) => {
        const cached = await cache.match(event.request);
        if (cached) return cached;
        try {
          const resp = await fetch(event.request);
          if (resp.ok && resp.status === 200) {  // nicht range-206
            cache.put(event.request, resp.clone());
            cache.keys().then(keys => {
              if (keys.length > 20) {
                keys.slice(0, keys.length - 20).forEach(k => cache.delete(k));
              }
            });
          }
          return resp;
        } catch (_) {
          return new Response('', {status: 503});
        }
      })
    );
    return;
  }


  // /api/recipes Liste: KEIN Cache. Stale-while-revalidate erzeugte das
  // klassische 'erste Anzeige = alte Daten'-Problem (User muss Tab wechseln
  // damit der Background-Fetch sichtbar wird). Performance kommt jetzt aus
  // HTTP-Cache-Headern + resized Thumbnails, nicht aus SW-Cache.
  // Cache wird beim Aktivieren des neuen SW automatisch geleert (siehe activate).

  // API + auth: immer network
  if (url.pathname.startsWith('/api/') || url.pathname === '/login' || url.pathname === '/logout') {
    return; // default browser handling
  }
  // Static: cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) =>
        cached || fetch(event.request).then((resp) => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
          }
          return resp;
        })
      )
    );
    return;
  }
  // Navigation: network-first
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/'))
    );
  }
});
