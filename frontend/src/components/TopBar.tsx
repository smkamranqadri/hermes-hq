import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { Bell } from './Bell'
import { useQuery } from '@tanstack/react-query'
import { Menu, MenuItem } from './Menu'
import { AppearanceMenu } from './AppearanceMenu'

// Five primary tabs. Reviews lives inside Tasks; Activity inside Overview/Project.
export const TABS = [
  ['Overview', '/'], ['Projects', '/projects'], ['Tasks', '/tasks'], ['Agents', '/agents'], ['Chat', '/chat'],
] as const
// Secondary surfaces behind the Tools menu (Group 5–7 + browsers).
export const TOOLS = [
  ['Files', '/files'], ['Terminal', '/terminal'], ['Memory', '/memory'],
  ['Skills', '/skills'], ['MCP', '/mcp'], ['Schedules', '/schedules'], ['Activity', '/activity'],
] as const

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id) }, [])
  return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// Full-width glass bar, same anatomy as the WM v0.9 topbar: brand · bordered pill nav · sysbar.
export function TopBar() {
  const nav = useNavigate()
  const clock = useClock()
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()), refetchInterval: 15000 })
  const live = sys.data?.dispatcher?.alive
  return (
    <header className="glass sticky top-0 z-20 flex flex-wrap items-center gap-x-3 border-x-0 border-t-0 px-3 py-1.5 sm:h-16 sm:gap-x-4 sm:px-4 sm:py-2 sm:flex-nowrap">
      <div className="flex h-10 min-w-0 shrink-0 items-center gap-2 sm:h-auto sm:gap-2.5" title="Hermes // HQ">
        <img src="/icon.svg" alt="" className="size-[26px] drop-shadow-[0_0_6px_var(--hq-accent-2)]" />
        <span className="hq-wordmark truncate text-xs font-bold uppercase sm:text-sm">Hermes // HQ</span>
        <span className="hidden rounded-full border border-line px-1.5 py-0.5 font-mono text-[10px] text-muted sm:inline">v{sys.data?.version ?? '—'}</span>
      </div>
      <nav className="relative z-0 order-last mb-1 hidden basis-full items-center justify-between gap-1 overflow-x-auto sm:flex rounded-full border border-line bg-glass p-1 [scrollbar-width:none] sm:order-none sm:mx-auto sm:mb-0 sm:basis-auto">
        {TABS.map(([label, to]) => (
          <NavLink key={to} to={to} end={to === '/'}
            className={({ isActive }) => clsx('rounded-full px-2.5 py-1 text-[13px] whitespace-nowrap transition-colors sm:px-3.5 sm:py-1.5 sm:text-sm',
              isActive ? 'bg-fg font-semibold text-bg' : 'text-muted hover:text-fg')}>
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="relative z-10 ml-auto flex h-10 shrink-0 items-center gap-1.5 text-xs text-muted sm:ml-0 sm:h-auto sm:gap-3">
        <span className="hidden sm:contents">
          <Menu button={<span>TOOLS ▾</span>}>
            {TOOLS.map(([label, to]) => <MenuItem key={to} onClick={() => nav(to)}>{label}</MenuItem>)}
          </Menu>
          <AppearanceMenu />
        </span>
        <span className="hidden sm:contents"><Bell /></span>
        <span className="flex items-center gap-2 font-mono text-[11px]">
          <span className={clsx('size-2 rounded-full', sys.isError ? 'bg-error text-error' : live ? 'hq-dot-live bg-working text-working' : 'bg-needsyou text-needsyou')} />
          <span>{sys.isError ? 'OFFLINE' : live ? 'LIVE' : 'PAUSED'}</span>
          {sys.data && typeof sys.data.running === 'number' && <span title={`${sys.data.running} of ${sys.data.cap} run slots busy`} className={clsx('ml-1 inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono text-[10px]', sys.data.paused ? 'border-line text-muted' : sys.data.running >= sys.data.cap ? 'border-needsyou/60 text-needsyou' : sys.data.running > 0 ? 'border-working/60 text-working' : 'border-line text-muted')}>{sys.data.running > 0 && !sys.data.paused && <span className="hq-dot-live size-1.5 rounded-full bg-current" />}{sys.data.running}/{sys.data.cap}</span>}
        </span>
        <span className="hidden font-mono text-[11px] md:inline">{clock}</span>
        <NavLink to="/system" className="hidden rounded-full border border-line px-2.5 py-1 font-mono text-[10px] hover:text-fg sm:inline">SYSTEM</NavLink>
      </div>
    </header>
  )
}
