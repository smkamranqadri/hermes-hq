import { NavLink } from 'react-router-dom'
import clsx from 'clsx'
import { useNotifications } from '../api'

const I = {
  overview: <path d="M3 12 12 4l9 8M5 10v10h5v-6h4v6h5V10" />,
  projects: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  tasks: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="m4 6 1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" /></>,
  chat: <path d="M21 12a8 8 0 0 1-8 8H8l-5 3 1.5-4.5A8 8 0 1 1 21 12z" />,
  brain: <><path d="M9.5 3a2.5 2.5 0 0 0-2.4 3.2A3 3 0 0 0 5 11a3 3 0 0 0 .6 5A3 3 0 0 0 11 19V5.5A2.5 2.5 0 0 0 9.5 3z" /><path d="M14.5 3a2.5 2.5 0 0 1 2.4 3.2A3 3 0 0 1 19 11a3 3 0 0 1-.6 5A3 3 0 0 1 13 19V5.5A2.5 2.5 0 0 1 14.5 3z" /></>,
  more: <><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></>,
}
// Brain took Inbox's slot (owner decision 2026-09-04): Inbox stays reachable
// under More, and its unread badge moves to the More tab.
export const TAB_ITEMS = [
  ['Overview', '/', 'overview'], ['Tasks', '/tasks', 'tasks'], ['Brain', '/brain', 'brain'], ['Chat', '/chat', 'chat'], ['More', '/more', 'more'],
] as const

/** Phone tab bar in the top nav's pill style: floating, rounded, glass. Inbox carries the unread badge. Hidden from `sm`. */
export function TabBar() {
  const n = useNotifications()
  const unread = n.data?.unread ?? 0
  return (
    <nav data-tabbar aria-label="Primary" className="hq-menu fixed inset-x-3 bottom-[calc(env(safe-area-inset-bottom,0px)+0.5rem)] z-30 flex rounded-full border border-line p-1 shadow-[0_12px_32px_rgba(0,0,0,0.45)] sm:hidden">
      {TAB_ITEMS.map(([label, to, icon]) => (
        <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => clsx('relative flex h-12 flex-1 flex-col items-center justify-center gap-0.5 rounded-full text-[10px] font-medium transition-colors', isActive ? 'bg-fg text-bg' : 'text-muted')}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{I[icon]}</svg>
          {label}
          {icon === 'more' && unread > 0 && <span data-tab-badge className="absolute right-[calc(50%-1.35rem)] top-1 min-w-4 rounded-full bg-needsyou px-1 text-center font-mono text-[9px] font-semibold leading-4 text-bg">{unread > 99 ? '99+' : unread}</span>}
        </NavLink>
      ))}
    </nav>
  )
}
