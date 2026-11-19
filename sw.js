// Service worker for the Stock Research PWA.
//
// Strategy is network-first with a cache fallback, deliberately -- the whole
// point of the leaderboard is live prices, so a stale-while-revalidate cache
// would routinely show yesterday's upside as if it were current. The cache
// exists only so the app opens at all with no connection.
//
// Bump CACHE on any change to PRECACHE or to this file's logic; the activate
// handler deletes every other version.
const CACHE = 'stock-research-v1';

// The shell only. Ticker dashboards are cached lazily as they are visited --
// there are ~150 of them and precaching every one would be a large download
// on a phone's cellular connection.
const PRECACHE = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/icon.svg',
  './icons/icon-192.png',
  './icons/apple-touch-icon.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      // addAll is atomic: one 404 would reject the whole install and leave the
      // app with no worker, so failures are tolerated per-entry.
      .then((cache) => Promise.allSettled(PRECACHE.map((u) => cache.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Only GETs, and only our own origin: never interpose on a cross-origin
  // quote API or on a POST.
  if (req.method !== 'GET') return;
  if (new URL(req.url).origin !== self.location.origin) return;

  event.respondWith(
    fetch(req)
      .then((res) => {
        // Opaque and error responses are not worth storing.
        if (res && res.ok && res.type === 'basic') {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('./index.html')))
  );
});
