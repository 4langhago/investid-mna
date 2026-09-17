// 캐시 이름을 올리면 activate 단계에서 예전 캐시가 지워진다.
const CACHE_NAME = "investid-v2";
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

// 매물 데이터와 판정·필터 코드는 캐시보다 항상 최신을 우선한다(네트워크 우선, 오프라인 시 캐시).
// 예전에는 live_data.js 만 네트워크 우선이라 한인 커뮤니티·OLX·사업체 매물과 app.js/db.js 가
// 캐시에서 나가, 재방문자는 매일 갱신되는 매물과 수정된 필터를 보지 못했다.
// 쿼리스트링(?v=)이 붙어도 매칭되도록 끝을 '$' 로 고정하지 않는다.
const NETWORK_FIRST_PATTERNS = [/\/js\/[\w-]+\.js(\?|$)/, /\/css\/[\w-]+\.css(\?|$)/,
                                /\/(index|admin)\.html(\?|$)/, /\/$/];

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

  const sameOrigin = new URL(request.url).origin === self.location.origin;
  const isNetworkFirst = sameOrigin && NETWORK_FIRST_PATTERNS.some((re) => re.test(request.url));

  if (isNetworkFirst) {
    // 브라우저 HTTP 캐시도 건너뛰고 서버에 재검증(ETag)한다. 네트워크 우선이어도 HTTP 캐시가
    // 예전 파일을 돌려주면 갱신된 매물이 보이지 않는다. 페이지 이동 요청은 Request 로 다시
    // 만들 수 없어(mode: navigate) 그대로 보낸다.
    const fresh = request.mode === "navigate"
      ? fetch(request)
      : fetch(request.url, { cache: "no-cache", credentials: "same-origin" });
    event.respondWith(
      fresh
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
