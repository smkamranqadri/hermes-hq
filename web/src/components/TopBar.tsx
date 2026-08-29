import { NavLink } from 'react-router-dom'
import clsx from 'clsx'
import { useQuery } from '@tanstack/react-query'

// Seven primary tabs; System is secondary (right side), per the IA contract.
export const TABS = [
  ['Overview', '/'], ['Projects', '/projects'], ['Tasks', '/tasks'], ['Chat', '/chat'],
  ['Agents', '/agents'], ['Reviews', '/reviews'], ['Activity', '/activity'],
] as const

export function TopBar() {
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()), refetchInterval: 15000 })
  const live = sys.data?.dispatcher?.alive
  return (
    <header className="sticky top-0 z-10 flex flex-wrap items-center gap-x-4 border-b border-line bg-panel px-4 sm:h-12">
      <div className="flex h-12 shrink-0 items-center gap-2 whitespace-nowrap font-semibold tracking-tight">
        <span className="text-accent">⬡</span> hermes-hq
        <span className="hidden rounded bg-line px-1.5 py-0.5 text-[10px] font-normal text-muted sm:inline">v{sys.data?.version ?? '…'}</span>
      </div>
      <nav className="order-last flex basis-full items-center gap-1 overflow-x-auto pb-2 [scrollbar-width:none] sm:order-none sm:mx-auto sm:basis-auto sm:pb-0">
        {TABS.map(([label, to]) => (
          <NavLink key={to} to={to} end={to === '/'}
            className={({ isActive }) => clsx('rounded-full px-3 py-1 text-sm whitespace-nowrap',
              isActive ? 'bg-fg text-bg font-medium' : 'text-muted hover:text-fg')}>
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="ml-auto flex h-12 shrink-0 items-center gap-3 text-xs text-muted sm:ml-0">
        <span className="flex items-center gap-1.5">
          <span className={clsx('size-2 rounded-full', sys.isError ? 'bg-needsyou' : live ? 'bg-working' : 'bg-backlog')} />
          {sys.isError ? 'offline' : live ? 'LIVE' : 'PAUSED'}
        </span>
        <NavLink to="/system" className="hover:text-fg">SYSTEM</NavLink>
      </div>
    </header>
  )
}
