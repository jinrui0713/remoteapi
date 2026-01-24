const CACHE_NAME = 'ytdip-manager-v2';
const MEDIA_CACHE = 'ytdlp-media-v1';

const ASSETS = [
    '/static/index.html',
    '/static/login.html',
    '/static/manifest.json',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js',
    'https://cdn.jsdelivr.net/npm/vue@3.2.47/dist/vue.global.prod.js',
    'https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS);
        })
    );
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // 1. Media Files (Downloads) - Serve from MEDIA_CACHE if available
    if (url.pathname.includes('/downloads/') || url.pathname.includes('/api/download/')) {
        event.respondWith(
            caches.open(MEDIA_CACHE).then((cache) => {
                return cache.match(event.request).then((response) => {
                    if (response) return response;
                    // If not in cache, fetch from network (but don't cache automatically, user must explicitly save)
                    return fetch(event.request);
                });
            })
        );
        return;
    }

    // 2. API Requests - Network First, falling back to nothing (API doesn't work offline usually)
    if (url.pathname.startsWith('/api/')) {
        return; 
    }

    // 3. Static Assets & App Shell - NETWORK ONLY (Disable Cache for now to fix header issues)
    // event.respondWith(
    //     caches.match(event.request).then((cachedResponse) => {
    //         const fetchPromise = fetch(event.request).then((networkResponse) => {
    //             caches.open(CACHE_NAME).then((cache) => {
    //                 cache.put(event.request, networkResponse.clone());
    //             });
    //             return networkResponse;
    //         });
    //         return cachedResponse || fetchPromise;
    //     })
    // );
    return; // Pass through to network (effectively disables SW interception for now)
});
                });
                return networkResponse;
            }).catch(() => {
                // Return offline page if needed, or just let it fail
            });
            return cachedResponse || fetchPromise;
        })
    );
});
