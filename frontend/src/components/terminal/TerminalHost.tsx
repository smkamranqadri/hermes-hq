// The one terminal surface, mounted once in the app shell and never unmounted while the app runs:
// full page on /terminal, a bottom panel elsewhere (desktop, Ctrl/Cmd+`), hidden otherwise — the xterm
// instances and their WebSockets survive every route change.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { GlassCard, PageHeader } from '../GlassCard'
import { Btn } from '../Modal'
import { Menu, MenuItem } from '../Menu'
import { termStore, useTermStore, type TermTab } from './store'
import { TerminalTab, type TermHandle } from './TerminalTab'

const useMobile = () => {
  const [m, setM] = useState(() => window.innerWidth < 640)
  useEffect(() => { const h = () => setM(window.innerWidth < 640); window.addEventListener('resize', h); return () => window.removeEventListener('resize', h) }, [])
  return m
}

export function TerminalHost() {
  const st = useTermStore()
  const loc = useLocation(); const nav = useNavigate()
  const mobile = useMobile()
  const onPage = loc.pathname === '/terminal'
  const mode: 'page' | 'panel' | 'hidden' = onPage ? 'page' : st.panelOpen && !mobile ? 'panel' : 'hidden'
  const refs = useRef(new Map<string, TermHandle>())
  const [find, setFind] = useState<string | null>(null)
  const [drag, setDrag] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)
  const [user, setUser] = useState<string>('')
  useEffect(() => { fetch('/api/terminal/sessions').then(r => r.json()).then(j => setUser(j.user ?? '')).catch(() => undefined) }, [])

  useEffect(() => { if (mode !== 'hidden' && st.tabs.length === 0) termStore.newTab() }, [mode, st.tabs.length])
  useEffect(() => { if (onPage) document.title = 'Terminal · Hermes HQ' }, [onPage])

  // Phones: size the card to the visible viewport so the keyboard never hides the prompt (same rule as Chat).
  useEffect(() => {
    if (mode !== 'page' || !mobile) return
    const vv = window.visualViewport; const card = cardRef.current
    const apply = () => {
      if (!card) return
      const vh = vv ? vv.height : window.innerHeight
      const kb = Math.max(0, Math.round(window.innerHeight - vh - (vv?.offsetTop ?? 0)))
      if (kb > 0) window.scrollTo({ top: 0 })
      const top = card.getBoundingClientRect().top + window.scrollY
      const barEl = document.querySelector('[data-tabbar]')
      const bar = kb > 0 || !barEl ? 0 : Math.max(0, vh - barEl.getBoundingClientRect().top) + 8
      card.style.height = `${Math.max(200, Math.round(vh - top - 12 - bar))}px`
      refs.current.get(termStore.get().active)?.fit()
    }
    apply(); const t = setTimeout(apply, 150)
    vv?.addEventListener('resize', apply); vv?.addEventListener('scroll', apply); window.addEventListener('resize', apply)
    return () => { clearTimeout(t); vv?.removeEventListener('resize', apply); vv?.removeEventListener('scroll', apply); window.removeEventListener('resize', apply); if (card) card.style.height = '' }
  }, [mode, mobile])

  // Panel drag-resize
  useEffect(() => {
    if (!drag) return
    const move = (e: MouseEvent) => termStore.setPanelHeight(Math.max(120, Math.min(Math.round(window.innerHeight * 0.7), window.innerHeight - e.clientY)))
    const up = () => setDrag(false)
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
  }, [drag])

  const active = refs.current.get(st.active)
  const closeTab = useCallback(async (t: TermTab) => {
    if (t.session) { try { await (await import('../../api')).post(`/api/terminal/${t.session}/close`) } catch { /* already gone */ } }
    termStore.closeTab(t.id)
  }, [])
  const rename = (t: TermTab) => { const n = window.prompt('Tab name', t.title); if (n?.trim()) termStore.update(t.id, { title: n.trim() }) }

  const strip = (
    <div className="relative z-20 flex min-w-0 items-center gap-1 border-b border-line px-1 py-1">
      <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none]">
        {st.tabs.map(t => (
          <div key={t.id} role="tab" aria-selected={t.id === st.active} onClick={() => termStore.setActive(t.id)} onDoubleClick={() => rename(t)}
            className={clsx('group flex shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px]', t.id === st.active ? 'border-accent/60 bg-accent/15 text-fg' : 'border-transparent text-muted hover:border-line hover:text-fg')}>
            <span className={clsx('size-1.5 rounded-full', t.exited != null ? 'bg-muted' : 'bg-working')} />
            <span className="max-w-[9rem] truncate">{t.title}</span>
            <button aria-label={`Close ${t.title}`} onClick={e => { e.stopPropagation(); closeTab(t) }} className="ml-0.5 rounded-full px-1 text-muted opacity-60 hover:bg-line hover:text-fg hover:opacity-100">×</button>
          </div>))}
        <button aria-label="New terminal" onClick={() => termStore.newTab()} className="shrink-0 rounded-full border border-line px-2 py-0.5 font-mono text-[11px] text-muted hover:text-fg">+</button>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {find != null
          ? <input autoFocus value={find} onChange={e => { setFind(e.target.value); active?.search(e.target.value, 1) }} placeholder="Find" aria-label="Find in terminal"
              onKeyDown={e => { if (e.key === 'Enter') active?.search(find, e.shiftKey ? -1 : 1); if (e.key === 'Escape') { setFind(null); active?.search('', 1); active?.focus() } }}
              className="w-28 rounded-full border border-line bg-inset px-2 py-0.5 font-mono text-[11px] outline-none focus:border-accent sm:w-36" />
          : <button aria-label="Find" title="Find (Ctrl+Shift+F)" onClick={() => setFind('')} className="rounded-full px-2 py-0.5 font-mono text-[11px] text-muted hover:text-fg">⌕</button>}
        <Menu button={<span className="font-mono text-[11px]">⋯</span>}>
          <MenuItem onClick={() => active?.restart()}>Restart shell</MenuItem>
          <MenuItem onClick={() => { const t = st.tabs.find(x => x.id === st.active); if (t) rename(t) }}>Rename tab</MenuItem>
          {mode === 'panel' && <MenuItem onClick={() => nav('/terminal')}>Open full page</MenuItem>}
          {mode === 'page' && !mobile && <MenuItem onClick={() => { termStore.setPanel(true); nav(-1) }}>Dock as panel</MenuItem>}
          <MenuItem onClick={() => { termStore.get().tabs.forEach(closeTab); termStore.setPanel(false) }}>Close all</MenuItem>
        </Menu>
        {mode === 'panel' && <button aria-label="Close panel" onClick={() => termStore.setPanel(false)} className="rounded-full px-2 py-0.5 font-mono text-[11px] text-muted hover:text-fg">×</button>}
      </div>
    </div>
  )

  const terms = (
    <div className="relative min-h-0 flex-1 p-2" onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'f') { e.preventDefault(); setFind(f => f ?? '') } }}>
      {st.tabs.map(t => <TerminalTab key={t.id} tab={t} visible={t.id === st.active && mode !== 'hidden'} mobile={mobile}
        ref={h => { if (h) refs.current.set(t.id, h); else refs.current.delete(t.id) }} />)}
    </div>
  )

  const keyRow = mobile && mode === 'page' && <MobileKeys onSend={d => active?.send(d)} />

  if (mode === 'page') {
    return (
      <section className="mx-auto max-w-7xl p-4 sm:p-6">
        <PageHeader crumb="terminal" title="Terminal" right={<div className="hidden items-center gap-2 font-mono text-[10px] text-muted sm:flex">
          <span className="rounded-full border border-line px-2 py-0.5" title="The Unix user the shell runs as (never root)">user <b className="text-fg">{user || '…'}</b></span>
          <span className="rounded-full border border-line px-2 py-0.5" title="On any other page, press Ctrl+` (Cmd+` on Mac) to open this terminal as a bottom panel">Ctrl+` <span className="text-fg">bottom panel</span></span>
        </div>} />
        <GlassCard className="flex h-[calc(100dvh-15.5rem)] min-h-[12rem] min-w-0 flex-col overflow-hidden !p-0 sm:h-[calc(100dvh-12.5rem)]">
          <div ref={cardRef} className="flex min-h-0 flex-1 flex-col">{strip}{terms}{keyRow}</div>
        </GlassCard>
      </section>
    )
  }
  return (
    <div className={clsx('glass fixed inset-x-0 bottom-0 z-30 hidden flex-col border-x-0 border-b-0 sm:flex', mode === 'hidden' && '!hidden')} style={{ height: st.panelHeight }} role="region" aria-label="Terminal panel">
      <div onMouseDown={() => setDrag(true)} className="h-1.5 w-full cursor-row-resize bg-transparent hover:bg-accent/40" aria-hidden="true" />
      {strip}{terms}
    </div>
  )
}

/** Phone key row: the on-screen keyboard cannot drive xterm directly, so text goes through an input. */
function MobileKeys({ onSend }: { onSend: (d: string) => void }) {
  const [v, setV] = useState('')
  const submit = () => { onSend(v + '\r'); setV('') }
  const K = ({ label, d, aria }: { label: string; d: string; aria?: string }) => <button aria-label={aria ?? label} onMouseDown={e => e.preventDefault()} onClick={() => onSend(d)} className="shrink-0 rounded-full border border-line px-2.5 py-1.5 font-mono text-[12px] text-muted active:bg-accent/20">{label}</button>
  return (
    <div className="border-t border-line p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
      <div className="mb-2 flex gap-1.5 overflow-x-auto [scrollbar-width:none]">
        {/* JSX attribute strings keep backslashes literally — control bytes must be JS string literals */}
        <K label="Tab" d={'\t'} /><K label="^C" d={'\x03'} aria="Control C" /><K label="Esc" d={'\x1b'} /><K label="↑" d={'\x1b[A'} aria="Up" /><K label="↓" d={'\x1b[B'} aria="Down" /><K label="←" d={'\x1b[D'} aria="Left" /><K label="→" d={'\x1b[C'} aria="Right" /><K label="^D" d={'\x04'} aria="Control D" /><K label="^L" d={'\x0c'} aria="Control L" />
        <button aria-label="Paste" onClick={async () => { try { const t = await navigator.clipboard.readText(); if (t) setV(x => x + t) } catch { /* denied */ } }} className="shrink-0 rounded-full border border-line px-2.5 py-1.5 font-mono text-[12px] text-muted">Paste</button>
      </div>
      <div className="flex gap-2">
        <input value={v} onChange={e => setV(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit() } }} placeholder="Type a command" aria-label="Command"
          autoCapitalize="off" autoCorrect="off" spellCheck={false} className="min-w-0 flex-1 rounded-full border border-line bg-inset px-3 py-2 font-mono text-sm outline-none focus:border-accent" />
        <Btn onClick={submit} aria-label="Send">↵</Btn>
      </div>
    </div>
  )
}
