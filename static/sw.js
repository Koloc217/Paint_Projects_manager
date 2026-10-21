/* Service worker Project Hub (отдаётся по адресу /sw.js, чтобы действовать на весь сайт).
 *
 * Правила простые, данные всегда свежие:
 * - /api/*, /photos/*, /login и любые запросы кроме GET идут только в сеть, ничего не кэшируется;
 * - страницы: сначала сеть; если сервер недоступен, показывается офлайн-заглушка;
 * - /static/*: сначала сеть (после обновления приложения сразу новые файлы), без сети — копия из кэша;
 * - внешние библиотеки и шрифты (Tailwind, Chart.js, Google Fonts): из кэша, параллельно обновляются.
 *
 * После правок этого файла увеличьте VERSION: старые кэши удалятся при активации.
 */
const VERSION = 'v1';
const STATIC_CACHE = `ph-static-${VERSION}`;
const CDN_CACHE = `ph-cdn-${VERSION}`;
const OFFLINE_URL = '/static/offline.html';
const PRECACHE = [
    OFFLINE_URL,
    '/static/icons/icon-192.png',
    '/static/icons/favicon-32.png',
];
const CDN_HOSTS = ['cdn.tailwindcss.com', 'cdn.jsdelivr.net', 'fonts.googleapis.com', 'fonts.gstatic.com'];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(STATIC_CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys
                .filter((k) => k.startsWith('ph-') && k !== STATIC_CACHE && k !== CDN_CACHE)
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

async function staleWhileRevalidate(event) {
    const cache = await caches.open(CDN_CACHE);
    const cached = await cache.match(event.request);
    const update = fetch(event.request)
        .then((response) => {
            if (response.ok || response.type === 'opaque') cache.put(event.request, response.clone());
            return response;
        })
        .catch(() => null);
    if (cached) {
        event.waitUntil(update);
        return cached;
    }
    const fresh = await update;
    return fresh || Response.error();
}

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);

    if (url.origin !== self.location.origin) {
        if (CDN_HOSTS.includes(url.hostname)) event.respondWith(staleWhileRevalidate(event));
        return;
    }
    if (request.mode === 'navigate') {
        event.respondWith(fetch(request).catch(async () => (await caches.match(OFFLINE_URL)) || Response.error()));
        return;
    }
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(networkFirst(request));
    }
    // /api/*, /photos/* и остальное — обычный запрос в сеть
});
