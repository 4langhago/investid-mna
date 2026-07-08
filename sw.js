const CACHE_NAME = "investid-v1";
const SHELL_ASSETS = [
  "./",
  "./index.html",
  "./admin.html",
  "./css/style.css",
  "./css/admin.css",
  "./js/app.js",
  "./js/db.js",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png"
];

// 실시간 매물 데이터는 캐시보다 항상 최신을 우선 (네트워크 우선)
const NETWORK_FIRST_PATTERNS = [/js\/live_data\.js$/, /js\/data\.js$/];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const isNetworkFirst = NETWORK_FIRST_PATTERNS.some((re) => re.test(request.url));

  if (isNetworkFirst) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        return response;
      });
    })
  );
});
