import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { useNotifications, markNotificationsRead, ago, type Notification } from '../api'

const TONE: Record<Notification['kind'], string> = { needs_you: 'bg-needsyou', done: 'bg-working', info: 'bg-queued', chat: 'bg-accent', question: 'bg-needsyou' }

/** Top-bar bell: unread count, dropdown with the latest notifications, click = open + mark read. */
export function Bell() {
  const q = useNotifications()
  const qc = useQueryClient(); const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { if (!open) return; const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }; document.addEventListener('mousedown', h); return () => document.removeEventListener('mousedown', h) }, [open])
  const unread = q.data?.unread ?? 0
  const rows = q.data?.notifications ?? []
  async function openItem(n: Notification) {
    setOpen(false)
    if (!n.read_at) { try { await markNotificationsRead([n.id]) } catch {} qc.invalidateQueries({ queryKey: ['notifications'] }) }
    if (n.href) nav(n.href)
  }
  async function readAll() { try { await markNotificationsRead() } catch {} qc.invalidateQueries({ queryKey: ['notifications'] }) }
  return (
    <div ref={ref} className="relative">
      <button type="button" aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`} data-bell onClick={() => setOpen(o => !o)} className={clsx('relative inline-flex h-7 w-7 items-center justify-center rounded-full border border-line hover:text-fg', open ? 'text-fg' : 'text-muted')}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></svg>
        {unread > 0 && <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-needsyou px-1 text-center font-mono text-[9px] font-semibold leading-4 text-bg" data-bell-count>{unread > 99 ? '99+' : unread}</span>}
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-40 w-96 rounded-xl border border-line bg-glass-strong p-2 text-xs shadow-lg max-sm:fixed max-sm:inset-x-2 max-sm:top-24 max-sm:w-auto" data-bell-menu>
          <div className="mb-1 flex items-center justify-between px-1"><span className="font-mono text-[10px] uppercase tracking-wider text-muted">Notifications{unread ? ` · ${unread} unread` : ''}</span>{unread > 0 && <button type="button" onClick={() => void readAll()} className="font-mono text-[10px] uppercase tracking-wider text-muted hover:text-fg">Mark all read</button>}</div>
          <ul className="max-h-[60vh] overflow-y-auto">
            {rows.length === 0 && <li className="px-2 py-3 text-muted">Nothing yet — you'll see tasks that need you, finished work and chat replies here.</li>}
            {rows.map(n => (
              <li key={n.id}>
                <button type="button" onClick={() => void openItem(n)} className={clsx('flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-raised', !n.read_at && 'bg-raised/60')}>
                  <span className={clsx('mt-1.5 size-1.5 shrink-0 rounded-full', TONE[n.kind] ?? 'bg-muted', n.read_at && 'opacity-40')} />
                  <span className="min-w-0 flex-1">
                    <span className={clsx('block truncate', !n.read_at && 'font-medium text-fg')}>{n.title}</span>
                    {n.body && <span className="block truncate text-muted">{n.body}</span>}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-muted">{ago(n.ts)}</span>
                </button>
              </li>))}
          </ul>
        </div>
      )}
    </div>
  )
}
