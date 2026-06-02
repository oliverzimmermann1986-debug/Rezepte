// Service Worker für Scrapper-PWA
// Strategy:
// - cache-first für /static/* (CSS, JS, Alpine)
// - cache-first stale-while-revalidate für /api/recipes/{id}/thumb
//   (Bilder ändern sich selten — Liste lädt instant aus dem Cache)
// - network-first für alles andere
// - Offline: Fallback auf cached '/'
const CACHE_NAME = 'scrapper-v2';
const THUMB_CACHE = 'scrapper-thumbs-v1';
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
        .filter(n => n !== CACHE_NAME && n !== THUMB_CACHE)
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
