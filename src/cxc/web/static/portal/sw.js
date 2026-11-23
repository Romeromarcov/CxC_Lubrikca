const CACHE_NAME = "lubrikca-portal-shell-v1";
const SHELL_URLS = ["/portal/", "/portal/manifest.webmanifest", "/portal/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS).catch(() => undefined)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // NUNCA cachear endpoints autenticados ni mutaciones de API
  if (url.pathname.startsWith("/api/") || event.request.method !== "GET") {
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() => caches.match("/portal/")),
    );
    return;
  }
  if (url.pathname.startsWith("/portal/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request)),
    );
  }
});

self.addEventListener("push", (event) => {
  let payload = { title: "Lubrikca Portal", body: "Tienes una actualización en tu cuenta.", data: { url: "/portal/" } };
  try {
    if (event.data) {
      const parsed = event.data.json();
      if (parsed && typeof parsed === "object") {
        payload = {
          title: parsed.title || payload.title,
          body: parsed.body || payload.body,
          data: parsed.data || payload.data,
        };
      }
    }
  } catch {
    const txt = event.data ? event.data.text() : "";
    if (txt) payload.body = txt;
  }
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: "/portal/icon.svg",
      badge: "/portal/icon.svg",
      data: payload.data,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/portal/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientsArr) => {
      for (const client of clientsArr) {
        if ("focus" in client && client.url.includes("/portal")) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
      return undefined;
    }),
  );
});
