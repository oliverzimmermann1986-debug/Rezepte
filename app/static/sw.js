// Service Worker für die Rezepte-PWA.
//
// Sicherheitsgrenze:
// - Authentifizierte API-Antworten, Rezeptbilder und Videos werden NICHT in
//   Cache Storage persistiert. Der Browser darf sie nur gemäß den privaten
//   HTTP-Cache-Headern halten.
// - Navigationen bleiben network-only, damit Login/Logout und Kontosperren
//   niemals durch einen alten App-Shell-Stand umgangen werden.
// - Ausschließlich öffentliche, unveränderliche Frontend-Assets erhalten einen
//   Offline-Fallback.
const CACHE_NAME = 'rezepte-static-v1.3.0-mise-en-place';
const STATIC_CACHE_URLS = [
  '/static/rezepte.css',
  '/static/app.js',
  '/static/runtime.js',
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
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', (event) => {
  if (event.data?.type !== 'CLEAR_PRIVATE_CACHES') return;
  event.waitUntil(
    caches.keys().then((names) => Promise.all(
      names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))
    ))
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);

  // API, Login/Logout und Navigation immer direkt zum Server. Das umfasst
  // insbesondere /api/recipes/{id}, Thumbnails und Range-Videoantworten.
  if (
    url.pathname.startsWith('/api/')
    || url.pathname === '/login'
    || url.pathname === '/logout'
    || event.request.mode === 'navigate'
  ) {
    return;
  }

  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json') {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      try {
        const response = await fetch(event.request, {cache: 'no-store'});
        if (response.ok) await cache.put(event.request, response.clone());
        return response;
      } catch (_) {
        return (await cache.match(event.request)) || new Response('', {status: 503});
      }
    })());
  }
});
