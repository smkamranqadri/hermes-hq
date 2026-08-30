import { NavLink } from 'react-router-dom'
import clsx from 'clsx'

const I = {
  overview: <path d="M3 12 12 4l9 8M5 10v10h5v-6h4v6h5V10" />,
  projects: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  tasks: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="m4 6 1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" /></>,
  agents: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  chat: <path d="M21 12a8 8 0 0 1-8 8H8l-5 3 1.5-4.5A8 8 0 1 1 21 12z" />,
  more: <><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></>,
}
export const TAB_ITEMS = [
  ['Overview', '/', 'overview'], ['Projects', '/projects', 'projects'], ['Tasks', '/tasks', 'tasks'], ['Agents', '/agents', 'agents'], ['Chat', '/chat', 'chat'], ['More', '/more', 'more'],
] as const

/** iOS-style bottom tab bar for phones (hidden from `sm`). Safe-area aware; height exposed as --hq-tabbar. */
export function TabBar() {
  return (
    <nav data-tabbar className="hq-menu fixed inset-x-0 bottom-0 z-30 flex border-t border-line pb-[env(safe-area-inset-bottom,0px)] sm:hidden" aria-label="Primary">
      {TAB_ITEMS.map(([label, to, icon]) => (
        <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => clsx('flex h-[3.25rem] flex-1 flex-col items-center justify-center gap-0.5 text-[10px] font-medium tracking-wide', isActive ? 'text-accent-2' : 'text-muted')}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{I[icon]}</svg>
          {label}
        </NavLink>
      ))}
    </nav>
  )
}
