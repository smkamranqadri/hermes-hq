/* hermes-hq service worker: Web Push + a static offline page. No app data is ever cached — the app must
   never show stale state; the only cached asset is /offline.html, shown when a NAVIGATION fails. */
const CACHE = 'hq-offline-v1'
self.addEventListener('install', e => e.waitUntil(
  caches.open(CACHE).then(c => c.addAll(['/offline.html'])).then(() => self.skipWaiting())))
self.addEventListener('activate', e => e.waitUntil(Promise.all([
  self.clients.claim(),
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))),
])))
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return // API calls, assets, everything else: straight to the network
  e.respondWith(fetch(e.request).catch(() => caches.match('/offline.html')))
})

self.addEventListener('push', e => {
  let d = {}
  try { d = e.data ? e.data.json() : {} } catch { d = { title: e.data ? e.data.text() : 'hermes-hq' } }
  const title = d.title || 'hermes-hq'
  const jobs = [self.registration.showNotification(title, {
    body: d.body || undefined, tag: d.tag || undefined, renotify: !!d.tag,
    icon: '/icon-192.png', badge: '/icon-192.png', data: { href: d.href || '/inbox', id: d.id },
  })]
  if (typeof d.unread === 'number' && 'setAppBadge' in self.navigator)
    jobs.push((d.unread > 0 ? self.navigator.setAppBadge(d.unread) : self.navigator.clearAppBadge()).catch(() => {}))
  e.waitUntil(Promise.all(jobs))
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
