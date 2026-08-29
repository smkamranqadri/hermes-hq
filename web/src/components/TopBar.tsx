import { useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { useQuery } from '@tanstack/react-query'
import { Menu, MenuItem } from './Menu'
import { THEMES, applyTheme, readThemePref, type ThemePref } from '../theme'

// Five primary tabs. Reviews lives inside Tasks; Activity inside Overview/Project.
export const TABS = [
  ['Overview', '/'], ['Projects', '/projects'], ['Tasks', '/tasks'], ['Agents', '/agents'], ['Chat', '/chat'],
] as const
// Secondary surfaces behind the Tools menu (Group 5–7 + browsers).
export const TOOLS = [
  ['Files', '/files'], ['Terminal', '/terminal'], ['Memory', '/memory'],
  ['Skills', '/skills'], ['MCP', '/mcp'], ['Schedules', '/schedules'],
] as const

export function TopBar() {
  const nav = useNavigate()
  const [theme, setTheme] = useState<ThemePref>(() => readThemePref())
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()), refetchInterval: 15000 })
  const live = sys.data?.dispatcher?.alive
  const pick = (t: ThemePref) => { setTheme(t); applyTheme(t) }
  return (
    <header className="sticky top-0 z-20 px-3 pt-3 sm:px-4">
      <div className="glass-strong flex flex-wrap items-center gap-x-4 rounded-xl px-3 shadow-[0_6px_16px_rgba(0,0,0,0.25)] sm:h-12 sm:px-4">
        <div className="flex h-12 shrink-0 items-center gap-2 whitespace-nowrap font-semibold tracking-tight">
          <span className="text-accent-2">⬡</span> hermes-hq
          <span className="hidden rounded bg-inset px-1.5 py-0.5 font-mono text-[10px] font-normal text-muted sm:inline">v{sys.data?.version ?? '…'}</span>
        </div>
        <nav className="order-last flex basis-full items-center gap-1 overflow-x-auto pb-2 [scrollbar-width:none] sm:order-none sm:mx-auto sm:basis-auto sm:pb-0">
          {TABS.map(([label, to]) => (
            <NavLink key={to} to={to} end={to === '/'}
              className={({ isActive }) => clsx('rounded-full px-3 py-1 text-sm whitespace-nowrap transition-colors',
                isActive ? 'bg-fg text-bg font-medium' : 'text-muted hover:bg-raised hover:text-fg')}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex h-12 shrink-0 items-center gap-1 text-xs text-muted sm:ml-0">
          <Menu button={<span>Tools ▾</span>}>
            {TOOLS.map(([label, to]) => <MenuItem key={to} onClick={() => nav(to)}>{label}</MenuItem>)}
          </Menu>
          <Menu button={<span title="Theme">◐</span>}>
            {THEMES.map(([id, label]) => <MenuItem key={id} active={theme === id} onClick={() => pick(id)}>{label}</MenuItem>)}
            <MenuItem active={theme === 'system'} onClick={() => pick('system')}>System</MenuItem>
          </Menu>
          <span className="ml-2 flex items-center gap-1.5 font-mono">
            <span className={clsx('size-2 rounded-full', sys.isError ? 'bg-error' : live ? 'bg-working' : 'bg-backlog')} />
            {sys.isError ? 'offline' : live ? 'LIVE' : 'PAUSED'}
          </span>
          <NavLink to="/system" className="ml-2 font-mono hover:text-fg">SYSTEM</NavLink>
        </div>
      </div>
    </header>
  )
}
