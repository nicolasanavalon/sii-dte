// Bandeja Zoho: recibe los avisos de correo nuevo (aunque la app esté cerrada) y pone la burbuja del ícono.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

// La app (página) queda guardada en el teléfono: abre al tiro con la copia guardada y se actualiza por detrás.
self.addEventListener("fetch", e => {
  const r = e.request;
  if (r.method !== "GET" || r.mode !== "navigate") return;
  e.respondWith((async () => {
    const c = await caches.open("correo-app"), clave = new URL("./", self.registration.scope).href;
    const guardada = await c.match(clave);
    const red = fetch(r).then(res => { if (res.ok) c.put(clave, res.clone()); return res; }).catch(() => null);
    if (guardada && !/[?&]v=/.test(r.url)) { e.waitUntil(red); return guardada; }   // ?v=… = versión nueva: desde internet
    return (await red) || guardada || Response.error();
  })());
});

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) {}
  e.waitUntil((async () => {
    if (typeof d.n === "number" && self.navigator && self.navigator.setAppBadge) {
      try { d.n > 0 ? await self.navigator.setAppBadge(d.n) : await self.navigator.clearAppBadge(); } catch (_) {}
    }
    await self.registration.showNotification(d.titulo || "Correo nuevo", {
      body: d.cuerpo || "", tag: d.tag || "correo", icon: "icono-192.png", badge: "icono-192.png", data: { url: "./", id: d.id || "" }
    });
  })());
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const id = (e.notification.data || {}).id;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(lista => {
    for (const c of lista) if ("focus" in c) { if (id) c.postMessage({ abrir: id }); return c.focus(); }
    return self.clients.openWindow(id ? "./#m=" + encodeURIComponent(id) : "./");
  }));
});
