'use strict';
const CACHE = 'anxing-shell-v2';
const FILES = [
  '/',
  '/static/style.css',
  '/static/fonts.css',
  '/static/app.js',
  '/static/icon.svg',
  '/static/public-map.js',
  '/static/vendor/leaflet/leaflet.js',
  '/static/vendor/leaflet/leaflet.css',
];
self.addEventListener('install', (event) =>
  event.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(FILES))
      .then(() => self.skipWaiting()),
  ),
);
self.addEventListener('activate', (event) =>
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k.startsWith('anxing-') && k !== CACHE).map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  ),
);
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (
    event.request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    !FILES.includes(url.pathname)
  )
    return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          event.waitUntil(caches.open(CACHE).then((c) => c.put(event.request, copy)));
        }
        return response;
      })
      .catch(() => caches.match(event.request)),
  );
});
