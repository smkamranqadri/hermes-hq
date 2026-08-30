/* hermes-hq service worker: Web Push only (no offline caching — the app must never show stale state). */
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()))

self.addEventListener('push', e => {
  let d = {}
  try { d = e.data ? e.data.json() : {} } catch { d = { title: e.data ? e.data.text() : 'hermes-hq' } }
  const title = d.title || 'hermes-hq'
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || undefined, tag: d.tag || undefined, renotify: !!d.tag,
    icon: '/icon-192.png', badge: '/icon-192.png', data: { href: d.href || '/inbox', id: d.id },
  }))
})

self.addEventListener('notificationclick', e => {
  e.notification.close()
  const href = (e.notification.data && e.notification.data.href) || '/inbox'
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    const same = all.find(c => new URL(c.url).origin === self.location.origin)
    if (same) { try { await same.focus() } catch {} ; if ('navigate' in same) { try { await same.navigate(href); return } catch {} } ; same.postMessage({ type: 'hq:navigate', href }); return }
    await self.clients.openWindow(href)
  })())
})
