// Service worker: app shell offline, never touches live data.
const VERSION = 'lt-1.0.1';
const SHELL = [
  '/',
  '/css/base.css',
  '/css/listener.css',
  '/js/listener.js',
  '/js/i18n.js',
  '/js/player.js',
  '/js/player-worklet.js',
  '/icons/icon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/fonts/barlow-condensed-600-latin.woff2',
  '/fonts/barlow-condensed-600-latin-ext.woff2',
  '/manifest.webmanifest',
];
const LIVE = /^\/(api|ws|stream|admin)(\/|$)|^\/healthz$/;
// Operator console is online-only: always fetch its files fresh.
const ADMIN = /^\/(css\/admin\.css|js\/(admin|theme|ingest-worklet)\.js)$/;

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== VERSION) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || LIVE.test(url.pathname) || ADMIN.test(url.pathname)) return;

  if (req.mode === 'navigate') {
    // Network first so a new version shows up immediately; cache when offline.
    event.respondWith((async () => {
      try {
        const res = await fetch(req);
        if (res.ok && url.pathname === '/') (await caches.open(VERSION)).put('/', res.clone());
        return res;
      } catch {
        return (await caches.match('/')) || Response.error();
      }
    })());
    return;
  }

  // Stale-while-revalidate for static assets.
  event.respondWith((async () => {
    const cache = await caches.open(VERSION);
    const cached = await cache.match(req);
    const fresh = fetch(req).then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    }).catch(() => cached || Response.error());
    event.waitUntil(fresh.then(() => {}, () => {}));
    return cached || fresh;
  })());
});
