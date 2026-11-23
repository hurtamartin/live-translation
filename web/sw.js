// Service worker: app shell offline, never touches live data.
const VERSION = 'lt-1.2.0';
const SHELL = [
  '/',
  '/css/base.css',
  '/css/listener.css',
  '/js/listener.js',
  '/js/i18n.js',
  '/js/ui.js',
  '/js/theme.js',
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
const ADMIN = /^\/(css\/admin\.css|js\/(admin|ingest-worklet)\.js)$/;
// How long a page load waits for the server before the cached app opens instead.
// A switched-off server often does not refuse the connection, so without this the
// phone would sit on a blank splash screen until the network stack gives up.
const NAV_TIMEOUT = 2500;

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
    // Network first so a new version shows up immediately; the cached shell when
    // the server is slow or unreachable, so the app always opens.
    event.respondWith((async () => {
      const cache = await caches.open(VERSION);
      const network = fetch(req).then((res) => {
        if (res.ok && url.pathname === '/') cache.put('/', res.clone());
        // A reverse proxy answers 502/503 while the app container is down: open the app anyway.
        if (res.status >= 500) throw new Error(`HTTP ${res.status}`);
        return res;
      });
      event.waitUntil(network.then(() => {}, () => {}));
      const cached = await cache.match('/');
      if (!cached) return network.catch(() => Response.error());
      let timer;
      const late = new Promise((resolve) => { timer = setTimeout(() => resolve(cached), NAV_TIMEOUT); });
      try {
        return await Promise.race([network, late]);
      } catch {
        return cached;
      } finally {
        clearTimeout(timer);
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
