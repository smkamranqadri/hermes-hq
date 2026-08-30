// Terminal tabs + panel state. Tiny on purpose: the app shell imports this for the Ctrl/Cmd+` shortcut
// while the xterm-heavy host stays a lazy chunk. Persisted so a reload reattaches the same PTY sessions.
import { useSyncExternalStore } from 'react'

export type TermTab = { id: string; title: string; session: string | null; exited: number | null }
export type TermState = { tabs: TermTab[]; active: string; panelOpen: boolean; panelHeight: number; counter: number; opened: boolean }

const KEY = 'hq-terminal-tabs'
const DEFAULT: TermState = { tabs: [], active: '', panelOpen: false, panelHeight: 320, counter: 0, opened: false }

function load(): TermState {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return DEFAULT
    const s = { ...DEFAULT, ...JSON.parse(raw) } as TermState
    s.opened = s.tabs.length > 0 || s.panelOpen
    if (!s.tabs.some(t => t.id === s.active)) s.active = s.tabs[0]?.id ?? ''
    return s
  } catch { return DEFAULT }
}

let state: TermState = load()
const subs = new Set<() => void>()
function set(patch: Partial<TermState>) {
  state = { ...state, ...patch }
  try { localStorage.setItem(KEY, JSON.stringify({ ...state, opened: undefined })) } catch { /* private mode */ }
  subs.forEach(f => f())
}
const uid = () => Math.random().toString(36).slice(2, 10)

export const termStore = {
  get: () => state,
  subscribe: (f: () => void) => { subs.add(f); return () => { subs.delete(f) } },
  newTab(): TermTab {
    const n = state.counter + 1
    const tab: TermTab = { id: uid(), title: `Terminal ${n}`, session: null, exited: null }
    set({ tabs: [...state.tabs, tab], active: tab.id, counter: n, opened: true })
    return tab
  },
  setActive: (id: string) => set({ active: id }),
  update: (id: string, patch: Partial<TermTab>) => set({ tabs: state.tabs.map(t => t.id === id ? { ...t, ...patch } : t) }),
  closeTab(id: string) {
    const tabs = state.tabs.filter(t => t.id !== id)
    const active = state.active === id ? (tabs[tabs.length - 1]?.id ?? '') : state.active
    set({ tabs, active })
  },
  setPanel: (open: boolean) => set({ panelOpen: open, opened: state.opened || open }),
  togglePanel: () => termStore.setPanel(!state.panelOpen),
  setPanelHeight: (h: number) => set({ panelHeight: h }),
  markOpened: () => { if (!state.opened) set({ opened: true }) },
}
export const useTermStore = () => useSyncExternalStore(termStore.subscribe, termStore.get)
