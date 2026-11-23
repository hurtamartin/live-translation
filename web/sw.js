// Service worker: app shell offline, never touches live data.
// The server replaces VERSION with a hash of the web files when it serves this
// file, so every release that changes a file installs a fresh offline copy on the
// phones and drops the old one. Nothing to bump by hand.
const VERSION = 'lt-dev';
const SHELL = [
  '/',
  '/css/base.css',
  '/css/listener.css',
  '/js/listener.js',
  '/js/i18n.js',
  '/js/ui.js',
  '/js/theme.js',
  '/js/viewport.js',
  '/js/player.js',
  '/js/player-worklet.js',
  '/icons/icon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/artwork-256.png',
  '/icons/artwork-512.png',
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

// Pages whose HTML just came from the server load their scripts and styles from
// the server too: right after a release, new HTML must never run with old cached
// scripts. Pages opened from the offline copy stay on the offline copy.
const fresh = new Set();
function markFresh(id) {
  if (!id) return;
  fresh.add(id);
  if (fresh.size > 32) fresh.delete(fresh.values().next().value);
}

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
      if (!cached) {
        return network.then((res) => { markFresh(event.resultingClientId); return res; }, () => Response.error());
      }
      let timer;
      const late = new Promise((resolve) => { timer = setTimeout(() => resolve(null), NAV_TIMEOUT); });
      try {
        const res = await Promise.race([network, late]);
        if (!res) return cached;
        markFresh(event.resultingClientId);
        return res;
      } catch {
        return cached;
      } finally {
        clearTimeout(timer);
      }
    })());
    return;
  }

  event.respondWith((async () => {
    const cache = await caches.open(VERSION);
    if (fresh.has(event.clientId)) {
      // The page itself is the current release: so must be its files.
      try {
        const res = await fetch(req);
        if (res.ok) event.waitUntil(cache.put(req, res.clone()));
        return res;
      } catch {
        return (await cache.match(req)) || Response.error();
      }
    }
    // Stale-while-revalidate for a page running from the offline copy.
    const cached = await cache.match(req);
    const update = fetch(req).then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    });
    event.waitUntil(update.then(() => {}, () => {}));
    return cached || update.catch(() => Response.error());
  })());
});
