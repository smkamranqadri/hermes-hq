// One xterm instance bound to one PTY session over the terminal WebSocket. Stays mounted (and connected)
// while hidden so switching tabs/pages never loses the shell; refits when shown again.
import { useEffect, useImperativeHandle, useRef, forwardRef } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { termStore, type TermTab } from './store'
import { getCsrf } from '../../api'

export type TermHandle = { send: (d: string) => void; focus: () => void; fit: () => void; search: (q: string, dir: 1 | -1) => void; restart: () => void }

const cssVar = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()
function theme() {
  const bg = cssVar('--hq-bg') || '#15151F', fg = cssVar('--hq-text') || '#F1F1F6', accent = cssVar('--hq-accent') || '#8B5CF6'
  const light = /^#([0-9a-f]{6})$/i.test(bg) && parseInt(bg.slice(1, 3), 16) + parseInt(bg.slice(3, 5), 16) + parseInt(bg.slice(5, 7), 16) > 380
  return {
    background: bg, foreground: fg, cursor: accent, cursorAccent: bg, selectionBackground: accent + '55',
    black: light ? '#2b2b3a' : '#1c1c28', brightBlack: light ? '#6b6b80' : '#7a7a90',
    red: '#e5484d', brightRed: '#ff6369', green: '#30a46c', brightGreen: '#3dd68c', yellow: '#d4a017', brightYellow: '#f5c542',
    blue: light ? '#2557b7' : '#6aa5ff', brightBlue: light ? '#3b6fd6' : '#8ebbff', magenta: accent, brightMagenta: cssVar('--hq-accent-2') || accent,
    cyan: '#12a594', brightCyan: '#3ec6b6', white: light ? '#3a3a4a' : '#d0d0dc', brightWhite: light ? '#16315f' : '#ffffff',
  }
}
const wsUrl = (q: string) => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/terminal/ws?${q}`

export const TerminalTab = forwardRef<TermHandle, { tab: TermTab; visible: boolean; mobile: boolean }>(function TerminalTab({ tab, visible, mobile }, ref) {
  const box = useRef<HTMLDivElement>(null)
  const term = useRef<Terminal | null>(null)
  const fit = useRef<FitAddon | null>(null)
  const search = useRef<SearchAddon | null>(null)
  const ws = useRef<WebSocket | null>(null)
  const session = useRef<string | null>(tab.session)
  const tries = useRef(0)
  const closed = useRef(false)
  const wantFresh = useRef(false)
  const refused = useRef(false)

  const closeOld = () => { const old = session.current; session.current = null; if (old) fetch(`/api/terminal/${old}/close`, { method: 'POST', headers: { 'x-csrf': getCsrf() } }).catch(() => undefined) }
  const send = (d: string) => { if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify({ t: 'i', d })) }
  const sendSize = () => { const t = term.current; if (t && ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify({ t: 'r', cols: t.cols, rows: t.rows })) }
  const doFit = () => { try { fit.current?.fit(); sendSize() } catch { /* not laid out yet */ } }

  const fresh = () => { wantFresh.current = true; refused.current = false; tries.current = 0; termStore.update(tab.id, { exited: null, session: null }); term.current?.reset(); ws.current?.close(); ws.current = null; closeOld(); connect() }

  const connect = () => {
    const t = term.current; if (!t || closed.current) return
    const sid = wantFresh.current ? '' : (session.current ?? '')
    wantFresh.current = false
    const sock = new WebSocket(wsUrl(`session=${encodeURIComponent(sid)}&cols=${t.cols}&rows=${t.rows}`))
    sock.binaryType = 'arraybuffer'
    ws.current = sock
    sock.onopen = () => { tries.current = 0 }
    sock.onmessage = ev => {
      if (typeof ev.data === 'string') {
        let m: { t: string; id?: string; reattach?: boolean; exited?: number | null; code?: number; reason?: string }
        try { m = JSON.parse(ev.data) } catch { return }
        if (m.t === 'err') {
          refused.current = true
          if (m.code === 4404) {
            session.current = null; termStore.update(tab.id, { session: null })
            const was = termStore.get().tabs.find(x => x.id === tab.id)?.exited
            if (was != null) { t.write('\r\n\x1b[2m[shell ended] — press Enter to start a new one\x1b[0m\r\n'); return }
            t.write('\r\n\x1b[2m[session gone — starting a new shell]\x1b[0m\r\n'); setTimeout(() => { refused.current = false; connect() }, 300)
          }
          else { termStore.update(tab.id, { exited: -1 }); t.write(`\r\n\x1b[31m[${m.reason || 'cannot start terminal'}]\x1b[0m — press Enter to retry\r\n`) }
          return
        }
        if (m.t === 'hello' && m.id) {
          session.current = m.id; termStore.update(tab.id, { session: m.id, exited: m.exited ?? null })
          if (m.reattach) { t.reset(); t.write('\x1b[2m[reattached]\x1b[0m\r\n') }
          if (m.exited != null) t.write(`\r\n\x1b[2m[process exited code=${m.exited}] — press Enter to start a new shell\x1b[0m\r\n`)
          sendSize()
        } else if (m.t === 'exit') {
          termStore.update(tab.id, { exited: m.code ?? -1 })
          t.write(`\r\n\x1b[2m[process exited code=${m.code}] — press Enter to start a new shell\x1b[0m\r\n`)
        }
      } else t.write(new Uint8Array(ev.data))
    }
    sock.onclose = ev => {
      if (closed.current || ws.current !== sock || refused.current) return
      if (ev.code === 1000) return
      // A handshake refused by the server (no session cookie / foreign Origin) surfaces as 1006 in browsers.
      if (tries.current >= 5) {
        termStore.update(tab.id, { exited: -1 })
        t.write('\r\n\x1b[31m[connection refused — sign in again or reload]\x1b[0m — press Enter to retry\r\n')
        fetch('/api/session').then(r => { if (r.status === 401) window.dispatchEvent(new Event('hq:unauthenticated')) }).catch(() => undefined)
        return
      }
      const wait = Math.min(8000, 600 * 2 ** Math.min(tries.current++, 4))
      t.write(`\r\n\x1b[2m[reconnecting…]\x1b[0m\r\n`)
      setTimeout(connect, wait)
    }
  }

  useEffect(() => {
    const el = box.current; if (!el) return
    const t = new Terminal({ cursorBlink: true, fontSize: mobile ? 12 : 13, fontFamily: '"JetBrains Mono", Menlo, Monaco, Consolas, monospace', theme: theme(), allowProposedApi: true, scrollback: 5000 })
    const f = new FitAddon(); const s = new SearchAddon()
    t.loadAddon(f); t.loadAddon(s); t.loadAddon(new WebLinksAddon((_e, uri) => window.open(uri, '_blank', 'noopener')))
    t.attachCustomKeyEventHandler(e => !((e.ctrlKey || e.metaKey) && e.key === '`'))   // app shortcut, never shell input
    t.open(el)
    term.current = t; fit.current = f; search.current = s
    t.onData(d => {
      const st = termStore.get().tabs.find(x => x.id === tab.id)
      if (st?.exited != null && d === '\r') { fresh(); return }
      send(d)
    })
    t.onResize(sendSize)
    const ro = new ResizeObserver(() => { if (el.offsetParent !== null) doFit() })
    ro.observe(el)
    const mo = new MutationObserver(() => { t.options.theme = theme() })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    requestAnimationFrame(() => { doFit(); connect() })
    return () => { closed.current = true; ro.disconnect(); mo.disconnect(); ws.current?.close(); t.dispose() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { if (visible) { requestAnimationFrame(() => { doFit(); term.current?.focus() }) } }, [visible]) // eslint-disable-line react-hooks/exhaustive-deps

  useImperativeHandle(ref, () => ({
    send, focus: () => term.current?.focus(), fit: doFit,
    search: (q, dir) => { if (!q) { search.current?.clearDecorations(); return } dir > 0 ? search.current?.findNext(q) : search.current?.findPrevious(q) },
    restart: fresh,
  }))

  return <div ref={box} className="h-full w-full min-w-0 [&_.xterm]:h-full [&_.xterm-viewport]:!bg-transparent" style={{ display: visible ? undefined : 'none' }} />
})
