/** Browser-side notification preferences: OS notifications (Notification API) and a soft chime. Browser-local. */
export type NotifyPrefs = { browser: boolean; sound: boolean }
const KEY = 'hq-notify'
export function loadPrefs(): NotifyPrefs { try { return { browser: false, sound: false, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') } } catch { return { browser: false, sound: false } } }
export function savePrefs(p: NotifyPrefs) { try { localStorage.setItem(KEY, JSON.stringify(p)) } catch {} }

export const supportsNotifications = () => typeof window !== 'undefined' && 'Notification' in window
export const permission = (): NotificationPermission | 'unsupported' => supportsNotifications() ? Notification.permission : 'unsupported'
export async function requestPermission(): Promise<NotificationPermission | 'unsupported'> {
  if (!supportsNotifications()) return 'unsupported'
  if (Notification.permission === 'granted') return 'granted'
  try { return await Notification.requestPermission() } catch { return Notification.permission }
}

/** Show an OS notification; returns false when not allowed. `onClick` runs after the window is focused. */
export function showBrowserNotification(title: string, body: string | undefined, tag: string, onClick?: () => void): boolean {
  if (!supportsNotifications() || Notification.permission !== 'granted') return false
  try {
    const n = new Notification(title, { body, tag, icon: '/icon-192.png', badge: '/icon-192.png' })
    n.onclick = () => { try { window.focus() } catch {} onClick?.(); n.close() }
    return true
  } catch { return false }
}

/** The tab is not what the user is looking at: hidden, or another window has focus. */
export const userIsAway = () => document.visibilityState !== 'visible' || !document.hasFocus()

let ctx: AudioContext | null = null
/** Two-note chime via WebAudio (no asset). Silent when the AudioContext is not allowed yet. */
export function chime(kind: 'info' | 'attention' = 'info') {
  try {
    ctx = ctx ?? new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)()
    if (ctx.state === 'suspended') void ctx.resume()
    const now = ctx.currentTime
    const notes = kind === 'attention' ? [880, 660] : [660, 880]
    notes.forEach((f, i) => {
      const o = ctx!.createOscillator(); const g = ctx!.createGain()
      o.type = 'sine'; o.frequency.value = f
      g.gain.setValueAtTime(0.0001, now + i * 0.12); g.gain.exponentialRampToValueAtTime(0.12, now + i * 0.12 + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.12 + 0.22)
      o.connect(g).connect(ctx!.destination); o.start(now + i * 0.12); o.stop(now + i * 0.12 + 0.25)
    })
  } catch {}
}
