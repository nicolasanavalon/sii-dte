// Bandeja Zoho: recibe los avisos de correo nuevo (aunque la app esté cerrada) y pone la burbuja del ícono.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) {}
  e.waitUntil((async () => {
    if (typeof d.n === "number" && self.navigator && self.navigator.setAppBadge) {
      try { d.n > 0 ? await self.navigator.setAppBadge(d.n) : await self.navigator.clearAppBadge(); } catch (_) {}
    }
    await self.registration.showNotification(d.titulo || "Correo nuevo", {
      body: d.cuerpo || "", tag: d.tag || "correo", icon: "icono-192.png", badge: "icono-192.png", data: { url: "./" }
    });
  })());
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(lista => {
    for (const c of lista) if ("focus" in c) return c.focus();
    return self.clients.openWindow("./");
  }));
});
