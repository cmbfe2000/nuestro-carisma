/* Service worker — Nuestro Carisma. VERSION lines are stamped by tools/build.py */
const DATA = 'f00cb98ef5';
const SHELL = '137795c3e2';
const SHELL_CACHE = 'nc-shell-' + SHELL;
const DATA_CACHE = 'nc-data-' + DATA;
const IMG_CACHE = 'nc-img';
const SHELL_FILES = [
  './', 'index.html', 'styles.css', 'app.js', 'search-worker.js', 'manifest.webmanifest',
  'icons/icon.svg', 'icons/mark-light.png', 'icons/logo.png', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png',
  'fonts/source-serif-4-latin-400-normal.woff2', 'fonts/source-serif-4-latin-400-italic.woff2',
  'fonts/source-serif-4-latin-600-normal.woff2', 'fonts/source-serif-4-latin-600-italic.woff2',
  'fonts/cormorant-garamond-latin-500-normal.woff2', 'fonts/cormorant-garamond-latin-500-italic.woff2',
  'fonts/cormorant-garamond-latin-600-normal.woff2', 'fonts/cormorant-garamond-latin-600-italic.woff2',
  'fonts/inter-latin-400-normal.woff2', 'fonts/inter-latin-500-normal.woff2', 'fonts/inter-latin-600-normal.woff2',
  'data/index.json'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(SHELL_FILES.map(u => new Request(u, { cache: 'reload' })))));
});
self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keep = [SHELL_CACHE, DATA_CACHE, IMG_CACHE];
    for (const k of await caches.keys()) if (!keep.includes(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});
self.addEventListener('message', e => { if (e.data === 'skipWaiting') self.skipWaiting(); });

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin === location.origin) {
    const p = url.pathname;
    if (p.includes('/data/c/') || p.endsWith('/data/search.json')) {
      e.respondWith((async () => {
        const c = await caches.open(DATA_CACHE);
        const hit = await c.match(req, { ignoreSearch: true });
        if (hit) return hit;
        const r = await fetch(req);
        if (r.ok) c.put(req, r.clone());
        return r;
      })());
      return;
    }
    if (p.endsWith('/data/index.json')) {
      // the index must match this shell's data version: serve the cached copy
      e.respondWith(caches.match(req, { ignoreSearch: true }).then(hit => hit || fetch(req)));
      return;
    }
    e.respondWith((async () => {
      const hit = await caches.match(req, { ignoreSearch: true });
      if (hit) return hit;
      try { return await fetch(req); }
      catch { if (req.mode === 'navigate') return caches.match('index.html'); throw new Error('offline'); }
    })());
    return;
  }
  // remote images from nuestrocarisma.org / YouTube thumbnails: cache-first
  if (req.destination === 'image') {
    e.respondWith((async () => {
      const c = await caches.open(IMG_CACHE);
      const hit = await c.match(req);
      if (hit) return hit;
      try { const r = await fetch(req, { mode: 'no-cors' }); c.put(req, r.clone()); return r; }
      catch { return new Response('', { status: 504 }); }
    })());
  }
});
