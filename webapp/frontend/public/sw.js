/*
 * ECNU·校园可视可答系统 Service Worker
 * 策略：同源静态资源"网络优先、缓存兜底"；/api/ 请求永不缓存（保证问答实时性）；
 * 跨域资源（高德地图 SDK/瓦片）不拦截。上线更新缓存时递增 CACHE 版本号即可。
 */
const CACHE = 'ecnu-vqa-cache-v1';
const PRECACHE = ['./', './index.html'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  // 只处理同源请求；API 走网络不缓存
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response && response.status === 200 && response.type === 'basic') {
          const clone = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, clone));
        }
        return response;
      })
      .catch(() =>
        caches.match(request).then((cached) => {
          if (cached) return cached;
          // 页面导航离线兜底到 index.html（hash 路由，单页即可恢复）
          if (request.mode === 'navigate') return caches.match('./index.html');
          return undefined;
        })
      )
  );
});
