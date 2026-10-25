/* Project Hub service worker (served at /sw.js so that it controls the whole site).
 *
 * Simple rules, data is always fresh:
 * - /api/*, /photos/*, /login and every non-GET request go to the network only, nothing is cached;
 * - pages: network first; if the server is unreachable, an offline page is shown;
 * - /static/*: network first (new files right after an app update), the cached copy when offline.
 *   Tailwind, Chart.js and the fonts are local files too (static/vendor, static/fonts), nothing comes from a CDN.
 *
 * After editing this file, increase VERSION: old caches are deleted on activation.
 */
const VERSION = 'v3';
const STATIC_CACHE = `ph-static-${VERSION}`;
const OFFLINE_URL = '/static/offline.html';
const PRECACHE = [
    OFFLINE_URL,
    '/static/icons/icon-192.png',
    '/static/icons/favicon-32.png',
    '/static/vendor/tailwind.css',
    '/static/vendor/chart.umd.min.js',
    '/static/fonts/fonts.css',
];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(STATIC_CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys
                .filter((k) => k.startsWith('ph-') && k !== STATIC_CACHE)     // also drops the old ph-cdn-* cache
                .map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

async function networkFirst(request) {
    const cache = await caches.open(STATIC_CACHE);
    try {
        const response = await fetch(request);
        if (response.ok) cache.put(request, response.clone());
        return response;
    } catch (e) {
        const cached = await cache.match(request);
        if (cached) return cached;
        throw e;
    }
}

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);

    if (url.origin !== self.location.origin) return;
    if (request.mode === 'navigate') {
        event.respondWith(fetch(request).catch(async () => (await caches.match(OFFLINE_URL)) || Response.error()));
        return;
    }
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(networkFirst(request));
    }
    // /api/*, /photos/* and everything else: a normal network request
});
