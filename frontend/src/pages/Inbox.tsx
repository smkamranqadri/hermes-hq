import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { useNotifications, markNotificationsRead, ago, type Notification } from '../api'
import { GlassCard } from '../components/GlassCard'
import { Empty, Loading, Label } from '../components/ui'
import { Btn } from '../components/Modal'
import { loadPrefs, savePrefs, permission, requestPermission, showBrowserNotification, supportsNotifications, chime, type NotifyPrefs } from '../components/notify'
import { usePageTitle } from '../usePageTitle'

const TONE: Record<Notification['kind'], string> = { needs_you: 'bg-needsyou', done: 'bg-working', info: 'bg-queued', chat: 'bg-accent', question: 'bg-needsyou' }

/** Phone Inbox tab: the bell's list as a page, plus the alert preferences. */
export function Inbox() {
  usePageTitle('Inbox')
  const q = useNotifications(); const qc = useQueryClient(); const nav = useNavigate()
  const rows = q.data?.notifications ?? []; const unread = q.data?.unread ?? 0
  const [prefs, setPrefs] = useState<NotifyPrefs>(loadPrefs)
  const [perm, setPerm] = useState(permission())
  const iosBrowser = /iPhone|iPad|iPod/.test(navigator.userAgent) && !window.matchMedia('(display-mode: standalone)').matches
  useEffect(() => { setPerm(permission()) }, [])
  const setPref = (patch: Partial<NotifyPrefs>) => { const next = { ...prefs, ...patch }; setPrefs(next); savePrefs(next) }
  async function toggleBrowser() {
    if (prefs.browser) { setPref({ browser: false }); return }
    const p = await requestPermission(); setPerm(p)
    if (p === 'granted') { setPref({ browser: true }); showBrowserNotification('hermes-hq notifications on', 'You will be alerted when a task needs you or an agent replies while you are away.', 'hq-test') }
  }
  async function open(n: Notification) { if (!n.read_at) { try { await markNotificationsRead([n.id]) } catch {} qc.invalidateQueries({ queryKey: ['notifications'] }) } if (n.href) nav(n.href) }
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Inbox{unread ? <span className="ml-2 align-middle font-mono text-xs text-needsyou">{unread} unread</span> : null}</h1>
        {unread > 0 && <Btn kind="ghost" onClick={async () => { try { await markNotificationsRead() } catch {} qc.invalidateQueries({ queryKey: ['notifications'] }) }}>Mark all read</Btn>}
      </div>
      {q.isLoading && <Loading rows={4} />}
      {q.data && rows.length === 0 && <Empty title="Nothing yet" note="Tasks that need you, finished work, chat replies and agent questions land here." />}
      {rows.length > 0 && (
        <GlassCard className="mb-5 p-0">
          {rows.map(n => (
            <button key={n.id} type="button" onClick={() => void open(n)} className={clsx('flex w-full items-start gap-3 border-b border-line-subtle px-4 py-3 text-left text-sm last:border-0 hover:bg-raised', !n.read_at && 'bg-raised/50')}>
              <span className={clsx('mt-2 size-2 shrink-0 rounded-full', TONE[n.kind] ?? 'bg-muted', n.read_at && 'opacity-40')} />
              <span className="min-w-0 flex-1">
                <span className={clsx('block', !n.read_at && 'font-medium')}>{n.title}</span>
                {n.body && <span className="mt-0.5 block text-xs text-muted">{n.body}</span>}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-muted">{ago(n.ts)}</span>
            </button>))}
        </GlassCard>
      )}
      <Label>Alerts</Label>
      <GlassCard className="mt-2 text-sm">
        <label className={clsx('flex items-start gap-3 py-1.5', (!supportsNotifications() || perm === 'denied') && 'opacity-60')}>
          <input type="checkbox" className="mt-1" data-pref-browser checked={prefs.browser && perm === 'granted'} disabled={!supportsNotifications() || perm === 'denied'} onChange={() => void toggleBrowser()} />
          <span><span className="block">Browser alerts</span><span className="block text-xs text-muted">{!supportsNotifications() ? (iosBrowser ? 'iPhone: add Hermes HQ to the Home Screen (Share → Add to Home Screen) and open it from there — Safari tabs cannot show notifications.' : 'This browser has no Notification API.') : perm === 'denied' ? 'Blocked — allow notifications for this site in the browser settings.' : 'System notifications when a task needs you or an agent replies while the app is not in front.'}</span></span>
        </label>
        <label className="flex items-start gap-3 py-1.5">
          <input type="checkbox" className="mt-1" data-pref-sound checked={prefs.sound} onChange={e => { setPref({ sound: e.target.checked }); if (e.target.checked) chime('info') }} />
          <span><span className="block">Sound</span><span className="block text-xs text-muted">Soft chime for new notifications and finished chat replies.</span></span>
        </label>
      </GlassCard>
    </section>
  )
}
