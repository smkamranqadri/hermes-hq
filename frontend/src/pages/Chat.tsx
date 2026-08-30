import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { useAgents, useAgentSessions, useSessionDetail, useProjects, startScopedChat, streamChat, steerTurn, updateSession, useModels, addNotification, post, get, ago, when, ApiError, type SseEvent, type ChatMessage, type ScopedSession, type SessionDetail, type TurnOptions } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Loading, Select } from '../components/ui'
import { ActionBtn } from '../components/forms'
import { Btn, TextArea } from '../components/Modal'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'
import { GatewayDot } from './Agents'
import { Markdown } from '../components/chat/Markdown'
import { ToolCard, Thinking, fmtTokens, type ToolView } from '../components/chat/Blocks'
import { SessionMenu, RenameDialog, SearchModal, FindBar } from '../components/chat/SessionTools'
import { loadPrefs, chime } from '../components/notify'
import { fileToAttachment, buildMessage, loadOpts, saveOpts, saveDraft, takeDraft, clearDraft, matchSlash, SLASH, type Attachment } from '../components/chat/composer'

type Live = { text: string; tools: ToolView[]; thinking: string; runId: string | null; error: string | null; startedAt: number }
const emptyLive = (): Live => ({ text: '', tools: [], thinking: '', runId: null, error: null, startedAt: Date.now() })

function Bubble({ role, children, ts, tokens }: { role: string; children: React.ReactNode; ts?: number | null; tokens?: number | null }) {
  const mine = role === 'user'
  return (
    <div className={clsx('group/msg flex min-w-0 flex-col', mine ? 'items-end' : 'items-start')}>
      <div className={clsx('min-w-0 max-w-[92%] rounded-2xl px-3.5 py-2 text-sm sm:max-w-[80%]', mine ? 'bg-accent/20 text-fg' : 'bg-raised text-fg')}>{children}</div>
      {(ts || tokens) ? <span className="mt-0.5 px-1 font-mono text-[10px] text-muted opacity-0 transition group-hover/msg:opacity-100">{ts ? when(ts) : ''}{tokens ? ` · ${fmtTokens(tokens)} tok` : ''}</span> : null}
    </div>
  )
}

/** Stored transcript → bubbles; an assistant row's tool_calls become cards, fed by the tool rows that follow. */
function Transcript({ rows, onChoose }: { rows: ChatMessage[]; onChoose?: (t: string) => void }) {
  const lastAssistant = [...rows].reverse().find(m => m.role === 'assistant' && m.content && m.content.trim())?.id
  const items = useMemo(() => {
    const out: React.ReactNode[] = []
    const pending: ToolView[] = []
    for (const m of rows) {
      if (m.role === 'system') continue
      if (m.role === 'tool' || (m.tool_name && m.role !== 'assistant')) {
        const i = pending.findIndex(t => t.name === m.tool_name && t.result == null)
        if (i >= 0) { pending[i].result = m.content ?? ''; pending[i].endedAt = m.timestamp ?? undefined; continue }
        out.push(<ToolCard key={m.id} t={{ key: String(m.id), name: m.tool_name ?? 'tool', state: 'completed', result: m.content ?? '', preview: (m.content ?? '').slice(0, 120) }} />)
        continue
      }
      if (m.reasoning) out.push(<Thinking key={`r${m.id}`} text={m.reasoning} />)
      if (m.tool_calls?.length) {
        for (const c of m.tool_calls) {
          const t: ToolView = { key: `${m.id}:${c.id ?? c.name}`, name: c.name, state: 'completed', args: c.arguments, result: null, startedAt: m.timestamp ?? undefined }
          pending.push(t); out.push(<ToolCard key={t.key} t={t} />)
        }
      }
      if (m.content && m.content.trim()) out.push(<Bubble key={m.id} role={m.role} ts={m.timestamp} tokens={m.token_count}>{m.role === 'assistant' ? <Markdown text={m.content} onChoose={onChoose} optionsDisabled={m.id !== lastAssistant} /> : <div className="whitespace-pre-wrap break-words">{m.content}</div>}</Bubble>)
    }
    return out
  }, [rows, onChoose, lastAssistant])
  return <>{items}</>
}

function ScopeChip({ scope, className }: { scope: { project_slug: string | null; project_name: string | null; task_id: number | null; task_title: string | null } | null | undefined; className?: string }) {
  if (!scope) return null
  const to = scope.task_id ? `/tasks/${scope.task_id}` : `/projects/${scope.project_slug}`
  const label = scope.task_id ? `Task #${scope.task_id}` : `Project ${scope.project_name ?? scope.project_slug}`
  return <Link to={to} title={scope.task_title ?? scope.project_name ?? ''} onClick={e => e.stopPropagation()} className={clsx('inline-flex shrink-0 items-center rounded-full border border-accent/50 bg-accent/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-accent-2 hover:bg-accent/20', className)}>{label}</Link>
}

/** Status line under the composer, shaped like a terminal statusline: model · ██░░ pct · window · cost · scope. Click for the breakdown. */
function ContextLine({ d, opts, onOptions }: { d: SessionDetail; opts: TurnOptions; onOptions: () => void }) {
  const [open, setOpen] = useState(false)
  const cost = d.actual_cost_usd || d.estimated_cost_usd
  const est = d.cost_estimate
  const c = d.context
  const pct = c?.pct ?? null
  const tone = pct == null ? 'text-muted' : pct >= 80 ? 'text-needsyou' : pct >= 50 ? 'text-queued' : 'text-working'
  const filled = pct == null ? 0 : Math.min(10, Math.round(pct / 10))
  const bar = '█'.repeat(filled) + '░'.repeat(10 - filled)
  const scope = d.scope?.task_id ? `#${d.scope.task_id}` : d.scope?.project_slug
  if (!d.model && !opts.model && !(c && c.used > 0)) return null
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 font-mono text-[10px] text-muted">
      <button type="button" onClick={() => setOpen(o => !o)} className="inline-flex flex-wrap items-center gap-x-3 hover:text-fg" title={c ? `context ≈ ${c.used.toLocaleString()} of ${c.limit ? c.limit.toLocaleString() : '?'} tokens — transcript ${c.transcript.toLocaleString()} + system overhead ${c.overhead.toLocaleString()} (${c.source}); click for the breakdown` : 'click for the breakdown'}>
        <span className="text-accent-2" onClick={e => { e.stopPropagation(); onOptions() }} title="Provider, model, reasoning effort and fast mode for this session">{opts.provider ? `${opts.provider}/` : ''}{opts.model || d.model}{opts.effort ? ` · ${opts.effort}` : ''}{opts.fast ? ' · fast' : ''}</span>
        {c && c.used > 0 && <span className={tone}>{bar} {pct != null ? `${pct < 1 ? '<1' : pct.toFixed(0)}%` : fmtTokens(c.used)}{c.limit ? <span className="text-muted"> {fmtTokens(c.limit)}</span> : null}</span>}
        {cost ? <span>${cost.toFixed(2)}</span> : est ? <span title={`≈ from models.dev prices for ${est.model}; Hermes reports this session as included`}>≈${est.usd.toFixed(2)}</span> : null}
        {scope && <span className="opacity-70">{scope}</span>}
        <span>{open ? '▾' : '▸'}</span>
      </button>
      {open && <>
        <span>context ≈{fmtTokens(c?.used)}{c?.limit ? ` of ${fmtTokens(c.limit)}` : ''} (transcript {fmtTokens(c?.transcript)} + system {fmtTokens(c?.overhead)})</span>
        <span>↓ in {fmtTokens(d.input_tokens)}</span><span>↑ out {fmtTokens(d.output_tokens)}</span>
        {d.cache_read_tokens ? <span>⟳ cache {fmtTokens(d.cache_read_tokens)}</span> : null}
        {est && !cost ? <span>cost ≈${est.usd.toFixed(3)} via models.dev ({est.model})</span> : null}
        <span className="truncate">{d.id}</span>
      </>}
    </div>
  )
}

/** On phones the virtual keyboard shrinks visualViewport but not 100dvh on every browser: publish the difference as
 *  --hq-kb so the chat card shrinks with it and the composer stays visible above the keyboard. */
function useKeyboardInset(box: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const vv = window.visualViewport
    const root = document.documentElement
    const apply = () => {
      const vh = vv ? vv.height : window.innerHeight
      const kb = Math.max(0, Math.round(window.innerHeight - vh - (vv?.offsetTop ?? 0)))
      root.style.setProperty('--hq-kb', `${kb}px`); if (kb > 0) window.scrollTo({ top: 0 })
      // phones: the chat card fills exactly to the bottom of the visible viewport (no fixed rem guess)
      const card = box.current?.parentElement
      if (card) { if (window.innerWidth < 640) { const top = card.getBoundingClientRect().top + window.scrollY; const bar = kb > 0 ? 0 : (document.querySelector('[data-tabbar]')?.getBoundingClientRect().height ?? 0); card.style.height = `${Math.max(224, Math.round(vh - top - 12 - bar))}px` } else card.style.height = '' }
    }
    apply(); const t = setTimeout(apply, 150)
    vv?.addEventListener('resize', apply); vv?.addEventListener('scroll', apply); window.addEventListener('resize', apply)
    return () => { clearTimeout(t); vv?.removeEventListener('resize', apply); vv?.removeEventListener('scroll', apply); window.removeEventListener('resize', apply); root.style.removeProperty('--hq-kb') }
  }, [box])
}

/** Model / reasoning effort / fast for this session; models suggested from models.dev, free text allowed. */
function OptionsPanel({ opts, current, profile, onChange, onClose }: { opts: TurnOptions; current: string | null; profile: string; onChange: (o: TurnOptions) => void; onClose: () => void }) {
  const [q, setQ] = useState(opts.model ?? '')
  const models = useModels('', opts.provider ?? '', profile)
  return (
    <div className="mt-2 grid gap-2 rounded-lg border border-line bg-inset p-2 text-xs sm:grid-cols-[auto_1fr_auto_auto_auto]">
      <label className="flex items-center gap-2"><span className="text-muted">Provider</span>
        <select value={opts.provider ?? ''} onChange={e => onChange({ provider: e.target.value || undefined })} className="hq-select max-w-[11rem] appearance-none rounded-md border border-line bg-glass py-1 pl-2 pr-7 font-mono text-[11px] outline-none focus:border-accent" title="Providers Hermes has credentials for (auth.json / config.yaml of this agent); blank = the agent's default">
          <option value="">default{(models.data?.providers ?? []).find(p => p.active) ? ` (${(models.data?.providers ?? []).find(p => p.active)!.name})` : ''}</option>{(models.data?.providers ?? []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label className="flex min-w-0 items-center gap-2"><span className="shrink-0 text-muted">Model</span>
        <input list="hq-models" value={q} placeholder={current ? `${current} (agent default)` : 'agent default'} title={models.data?.models.length ? `${models.data.models.length} models Hermes lists for ${models.data.provider}` : 'Hermes lists no models for this provider — type an id'} onChange={e => setQ(e.target.value)} onBlur={() => onChange({ model: q.trim() || undefined })} onKeyDown={e => { if (e.key === 'Enter') { onChange({ model: q.trim() || undefined }); onClose() } }} className="min-w-0 flex-1 rounded-md border border-line bg-glass px-2 py-1 font-mono text-[11px] outline-none focus:border-accent" />
        <datalist id="hq-models">{(models.data?.models ?? []).map(m => <option key={m.id} value={m.id}>{m.description || undefined}</option>)}</datalist></label>
      <label className="flex items-center gap-2"><span className="text-muted">Reasoning</span>
        <select value={opts.effort ?? ''} onChange={e => onChange({ effort: e.target.value || undefined })} className="hq-select appearance-none rounded-md border border-line bg-glass py-1 pl-2 pr-7 font-mono text-[11px] outline-none focus:border-accent">
          <option value="">default</option>{(models.data?.efforts ?? ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']).map(e => <option key={e} value={e}>{e}</option>)}</select></label>
      <label className="flex items-center gap-2 text-muted"><input type="checkbox" checked={!!opts.fast} onChange={e => onChange({ fast: e.target.checked || undefined })} /> fast</label>
      <div className="flex items-center gap-2"><button type="button" onClick={() => { setQ(''); onChange({ model: undefined, provider: undefined, effort: undefined, fast: undefined }) }} className="text-muted hover:text-fg">reset</button><button type="button" onClick={onClose} className="text-muted hover:text-fg">✕</button></div>
    </div>
  )
}

const STARTERS: Record<string, string[]> = {
  orchestrator: ['What is blocked right now and what do you need from me?', 'Summarize the state of every project in three lines each.', 'Which tasks should run next, and why?'],
  reviewer: ['What did you flag in your last review?', 'What are the recurring quality issues across tasks?', 'Review policy: what would you tighten?'],
  coder: ['What did you change in your last run?', 'What is failing in the test suite right now?', 'List the open technical debt you noticed.'],
}
const DEFAULT_STARTERS = ['What did you do in your last session?', 'What are you good at, in three lines?', 'What information do you need from me to work well?']

export function Chat() {
  const { profile, id } = useParams()
  const nav = useNavigate(); const qc = useQueryClient(); const toast = useToast(); const loc = useLocation()
  useEffect(() => { if (!profile) nav('/chat/orchestrator', { replace: true }) }, [profile, nav])
  const agents = useAgents()
  const projects = useProjects()
  const [starting, setStarting] = useState(false)
  const agent = agents.data?.agents.find(a => a.name === profile)
  const sessions = useAgentSessions(profile)
  const detail = useSessionDetail(profile, id)
  usePageTitle(profile ? `Chat · ${profile}` : 'Chat')
  const [draft, setDraft] = useState('')
  const [live, setLive] = useState<Live | null>(null)
  const liveRef = useRef<Live | null>(null); liveRef.current = live
  const turnRef = useRef({ text: '', runId: '' })   // survives unmount (state updates stop once the page is left)
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  const box = useRef<HTMLDivElement>(null)
  // stick-to-bottom unless the reader scrolled up; count what arrived while they were up
  const [atBottom, setAtBottom] = useState(true)
  const [unread, setUnread] = useState(0)
  const rowCount = (detail.data?.transcript.length ?? 0) + (live ? 1 : 0)
  const lastSeen = useRef(0)
  const scrollDown = useCallback(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; setUnread(0); setAtBottom(true) }, [])
  useEffect(() => {
    const el = box.current; if (!el) return
    const onScroll = () => { const near = el.scrollHeight - el.scrollTop - el.clientHeight < 48; setAtBottom(near); if (near) setUnread(0) }
    el.addEventListener('scroll', onScroll, { passive: true }); return () => el.removeEventListener('scroll', onScroll)
  }, [id])
  useEffect(() => { lastSeen.current = 0; setUnread(0); setAtBottom(true) }, [id])
  useEffect(() => {
    const el = box.current; if (!el) return
    if (!atBottom) { if (rowCount > lastSeen.current) setUnread(u => u + (rowCount - lastSeen.current)); lastSeen.current = rowCount; return }
    lastSeen.current = rowCount
    const down = () => { el.scrollTop = el.scrollHeight }
    down(); const raf = requestAnimationFrame(down); const t = setTimeout(down, 200)
    const ro = new ResizeObserver(down); ro.observe(el); Array.from(el.children).forEach(c => ro.observe(c))
    return () => { cancelAnimationFrame(raf); clearTimeout(t); ro.disconnect() }
  }, [rowCount, live?.text, live?.tools.length, live?.thinking, pendingUser, id, atBottom])
  const installed = useMemo(() => (agents.data?.agents ?? []).filter(a => a.installed), [agents.data])
  const busy = live !== null
  const [rename, setRename] = useState<{ id: string; title: string } | null>(null)
  const [sheet, setSheet] = useState(false)
  useEffect(() => { setSheet(false) }, [id, profile])
  useKeyboardInset(box)
  const [atts, setAtts] = useState<Attachment[]>([])
  const [opts, setOpts] = useState<TurnOptions>({})
  const [showOpts, setShowOpts] = useState(false)
  const [down, setDown] = useState<{ msg: string; retry: () => void } | null>(null)
  const [restored, setRestored] = useState<'draft' | 'pending' | null>(null)
  const [slashSel, setSlashSel] = useState(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const slash = useMemo(() => matchSlash(draft), [draft])
  useEffect(() => { setSlashSel(0) }, [slash?.length])
  // per-session options + unsent text come back after a reload
  useEffect(() => {
    if (!profile) return
    setOpts(loadOpts(profile, id)); setDown(null)
    if (abort.current) return   // we navigated here ourselves while sending (new session): keep the composer as it is
    setAtts([])
    const d = takeDraft(profile, id); if (d) { setDraft(d.text); setRestored(d.pending ? 'pending' : 'draft') } else { setDraft(''); setRestored(null) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile, id])
  const setOpt = (patch: TurnOptions) => { if (!profile) return; const next = { ...opts, ...patch }; setOpts(next); saveOpts(profile, id, next) }
  async function addFiles(files: FileList | File[] | null | undefined) {
    if (!files) return
    for (const f of Array.from(files)) { try { const a = await fileToAttachment(f); setAtts(x => x.length >= 4 && a.kind === 'image' ? x : [...x, a]) } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } }
  }
  const [search, setSearch] = useState(false)
  const findSeed = (loc.state as { find?: string } | null)?.find
  const [find, setFind] = useState<string | null>(null)
  useEffect(() => { if (findSeed && id) { setFind(findSeed); nav(loc.pathname, { replace: true, state: null }) } // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [findSeed, id])
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setSearch(true) }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && id) { e.preventDefault(); setFind(f => f ?? '') }
      else if (e.key === 'Escape') { setFind(null); setSearch(false) }
    }
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h)
  }, [id])

  /** hq-owned slash commands; returns true when the text was consumed. */
  async function runSlash(text: string): Promise<boolean> {
    const [head, ...rest] = text.trim().split(/\s+/); const arg = rest.join(' ').trim(); const cmd = head.toLowerCase()
    if (!SLASH.some(s => s.cmd === cmd)) return false
    const needSession = () => { if (!id) { toast('Start the session first', 'err'); return false } return true }
    switch (cmd) {
      case '/help': toast(SLASH.map(s => `${s.cmd}${s.args ? ' ' + s.args : ''} — ${s.desc}`).join('\n')); break
      case '/model': setOpt({ model: arg || undefined }); toast(arg ? `Model for this session: ${arg}` : 'Model: gateway default'); break
      case '/reasoning': if (!arg) { setShowOpts(true); break } setOpt({ effort: arg === 'default' ? undefined : arg }); toast(`Reasoning effort: ${arg}`); break
      case '/fast': setOpt({ fast: arg !== 'off' }); toast(arg === 'off' ? 'Fast mode off' : 'Fast mode on'); break
      case '/title': if (!needSession()) break; if (!arg) { setRename({ id: id!, title: detail.data?.title || '' }); break } try { await updateSession(profile!, id!, { title: arg }); qc.invalidateQueries({ queryKey: ['agent-sessions', profile] }); qc.invalidateQueries({ queryKey: ['session', profile, id] }) } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err') } break
      case '/pin': case '/unpin': if (!needSession()) break; try { await updateSession(profile!, id!, { pinned: cmd === '/pin' }); qc.invalidateQueries({ queryKey: ['agent-sessions', profile] }) } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err') } break
      case '/export': if (!needSession()) break; window.open(`/api/session/${profile}/${id}/export.md`, '_blank'); break
      case '/find': if (!needSession()) break; setFind(arg); break
      case '/new': nav(`/chat/${profile}`); break
      case '/steer': if (!live?.runId) { toast('Nothing is running to steer', 'err'); break } if (!arg) break; try { await steerTurn(profile!, id!, live.runId, arg); toast('Steer sent') } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err') } break
    }
    return true
  }
  async function send(text?: string) {
    const raw = (text ?? draft).trim()
    if (!profile) return
    // busy + a run id = steer the running turn instead of a new message
    if (busy) {
      if (!raw || !id || !live?.runId) return
      const steerText = raw.startsWith('/steer ') ? raw.slice(7).trim() : raw
      try { await steerTurn(profile, id, live.runId, steerText); setDraft(''); clearDraft(profile, id); toast('Steer sent to the running turn') } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err') }
      return
    }
    if (raw.startsWith('/') && text === undefined && await runSlash(raw)) { setDraft(''); clearDraft(profile, id); return }
    const attsNow = text === undefined ? atts : []
    if (!raw && attsNow.length === 0) return
    const msg = raw || (attsNow.some(a => a.kind === 'image') ? 'Describe the attached image.' : ' ')
    let sid = id
    try {
      if (!sid) { const s = await post<{ id: string }>(`/api/chat/${profile}/sessions`, { title: msg.slice(0, 60) }); sid = s.id; saveOpts(profile, sid, opts); clearDraft(profile, undefined); nav(`/chat/${profile}/${sid}`, { replace: true }) }
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err'); return }
    const payload = buildMessage(msg, attsNow)
    if (text === undefined) { setDraft(''); setAtts([]); setRestored(null) }
    saveDraft(profile, sid, msg, true)
    setPendingUser(msg + (attsNow.length ? `\n${attsNow.map(a => a.kind === 'image' ? '🖼 ' + a.name : '📄 ' + a.name).join('  ')}` : '')); setLive(emptyLive()); setAtBottom(true); setDown(null)
    let failed = false
    abort.current = new AbortController()
    turnRef.current = { text: '', runId: '' }
    const onEvent = (e: SseEvent) => setLive(l => {
      const d = e.data as Record<string, string | undefined>
      if (e.name === 'run.started' && d.run_id) turnRef.current.runId = d.run_id
      if (e.name === 'assistant.delta') turnRef.current.text += d.delta ?? ''
      if (!l) return l
      switch (e.name) {
        case 'run.started': return { ...l, runId: (d.run_id as string) ?? l.runId }
        case 'assistant.delta': return { ...l, text: l.text + (d.delta ?? '') }
        case 'tool.progress': return { ...l, thinking: (l.thinking + (d.delta ?? '')).slice(-4000) }
        case 'tool.started': return { ...l, tools: [...l.tools, { key: `${l.tools.length}`, name: d.tool_name ?? 'tool', state: 'started', preview: d.preview, args: typeof d.args === 'string' ? d.args : d.args ? JSON.stringify(d.args) : undefined, startedAt: Date.now() / 1000 }] }
        case 'tool.completed': case 'tool.failed': {
          const tools = [...l.tools]; const i = tools.map(t => t.state).lastIndexOf('started')
          if (i >= 0) tools[i] = { ...tools[i], state: e.name === 'tool.failed' ? 'failed' : 'completed', preview: d.preview ?? tools[i].preview, endedAt: Date.now() / 1000 }
          return { ...l, tools }
        }
        case 'error': return { ...l, error: d.message ?? 'error' }
        default: return l
      }
    })
    try { await streamChat(profile, sid!, payload, onEvent, abort.current.signal, opts) }
    catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        failed = true; const m = e instanceof ApiError ? e.message : String(e)
        if (e instanceof ApiError && (e.status === 502 || e.status === 0)) setDown({ msg: m, retry: () => { setDown(null); void send(msg) } }); else toast(m, 'err')
      }
    }
    finally {
      const finalText = turnRef.current.text || liveRef.current?.text || ''; const runId = turnRef.current.runId || liveRef.current?.runId || ''
      const away = document.hidden || !window.location.pathname.endsWith(`/chat/${profile}/${sid}`)
      const asked = /```hq-options/.test(finalText)
      if (!failed && !away && loadPrefs().sound) chime(asked ? 'attention' : 'info')
      if (!failed && (away || asked)) {
        void addNotification({ kind: asked ? 'question' : 'chat', title: asked ? `${profile} asked you a question` : `${profile} replied`, body: finalText.replace(/```[\s\S]*?```/g, '').trim().slice(0, 200) || undefined, href: `/chat/${profile}/${sid}`, source_key: `chat:${sid}:${runId || Date.now()}` })
          .then(() => qc.invalidateQueries({ queryKey: ['notifications'] })).catch(() => {})
      }
      abort.current = null; setLive(null); setPendingUser(null)
      if (failed) { setDraft(msg); saveDraft(profile, sid, msg) } else clearDraft(profile, sid)
      qc.invalidateQueries({ queryKey: ['session', profile, sid] }); qc.invalidateQueries({ queryKey: ['agent-sessions', profile] }); qc.invalidateQueries({ queryKey: ['agents'] })
    }
  }
  /** Project picker: resume this agent's latest chat about the project, or start a new one (seeded with the brief) when none exists / forced. */
  async function openProject(slug: string, fresh = false) {
    const p = projects.data?.projects.find(x => x.slug === slug)
    if (!profile || !p || starting || busy) return
    setStarting(true)
    try {
      if (!fresh) {
        const { sessions } = await get<{ sessions: ScopedSession[] }>(`/api/project/${slug}/chat-sessions`)
        const last = sessions.find(x => x.profile === profile && !x.task_id)
        if (last) { nav(`/chat/${profile}/${last.session_id}`); return }
      }
      const s = await startScopedChat({ profile, project_id: p.id }); qc.invalidateQueries({ queryKey: ['agent-sessions', profile] }); qc.invalidateQueries({ queryKey: ['chat-scoped'] })
      nav(`/chat/${s.profile}/${s.id}`, { state: { seed: s.brief } })
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err') }
    finally { setStarting(false) }
  }
  // A scoped session arrives with its brief in router state: send it as the first visible turn, once.
  const seed = (loc.state as { seed?: string } | null)?.seed
  const seeded = useRef<string | null>(null)
  useEffect(() => {
    if (!seed || !id || !profile || busy || seeded.current === id) return
    seeded.current = id
    nav(loc.pathname, { replace: true, state: null })
    void send(seed)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, id, profile])
  async function stop() {
    if (!profile || !id || !live) return
    if (live.runId) { try { await post(`/api/chat/${profile}/${id}/stop/${live.runId}`) } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err') } }
    abort.current?.abort()
  }

  const agentKnown = !!agent
  const chatDisabled = agentKnown && agent.name !== 'orchestrator' && !agent.gateway.enabled
  const liveRun = detail.data?.live_run ?? null
  const starters = (profile && STARTERS[profile]) || DEFAULT_STARTERS
  const waitingFirstToken = live && !live.text && live.tools.length === 0 && !live.thinking
  /** Agent select + New session + pinned/recent list + search — the sidebar on desktop, the bottom sheet on phones. */
  const sessionNav = profile && (
    <>
      <div className="flex items-center justify-between gap-2">
        <Select value={profile} onChange={e => nav(e.target.value ? `/chat/${e.target.value}` : '/chat')} className="min-w-0 max-w-[12rem] lg:max-w-[9rem]" aria-label="Agent">
          {installed.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
        </Select>
        {agent && <GatewayDot g={agent.gateway} />}
      </div>
      <Link to={`/chat/${profile}`} className={clsx('mt-2 block rounded-lg px-2 py-1.5 text-xs hover:bg-raised', !id && 'bg-raised')}>+ New session</Link>
      {sessions.isLoading && <Loading rows={4} />}
      <ul className="mt-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto" data-session-list>
        {(() => { const all = sessions.data?.sessions ?? []; const pinned = all.filter(s => s.pinned); const rest = all.filter(s => !s.pinned)
          const row = (s: typeof all[number]) => (
            <li key={s.id} className="group/sess flex min-w-0 items-center gap-1">
              <Link to={`/chat/${profile}/${s.id}`} className={clsx('block min-w-0 flex-1 truncate rounded-lg px-2 py-1.5 text-xs hover:bg-raised', s.id === id && 'bg-raised')} title={s.id}>{s.pinned ? <span className="mr-1 text-[10px] text-muted">📌</span> : null}{s.scope && <span className="mr-1 font-mono text-[10px] text-accent-2">{s.scope.task_id ? `#${s.scope.task_id}` : s.scope.project_slug}</span>}{s.title || s.id}<span className="ml-1 text-[10px] text-muted">{ago(s.last_activity_at ?? s.started_at)}</span></Link>
              <SessionMenu profile={profile} s={s} current={s.id === id} onRename={() => setRename({ id: s.id, title: s.title || '' })} />
            </li>)
          return <>{pinned.length > 0 && <li className="px-2 pt-1 font-mono text-[10px] uppercase tracking-wider text-muted">Pinned</li>}{pinned.map(row)}{pinned.length > 0 && rest.length > 0 && <li className="px-2 pt-2 font-mono text-[10px] uppercase tracking-wider text-muted">Recent</li>}{rest.map(row)}</>
        })()}
      </ul>
      <button type="button" onClick={() => { setSheet(false); setSearch(true) }} className="mt-2 flex items-center justify-between rounded-lg border border-line px-2 py-1 text-[11px] text-muted hover:bg-raised hover:text-fg"><span className="inline-flex items-center gap-1.5"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.6-3.6" /></svg>Search all chats</span><kbd className="font-mono text-[10px]">Ctrl K</kbd></button>
    </>
  )
  return (
    <section className="mx-auto flex max-w-6xl flex-col p-4 sm:p-6">
      <PageHeader crumb="chat" title="Chat" right={<div className="flex flex-wrap items-center gap-2">
        {id && <ScopeChip scope={detail.data?.scope} />}
        {id && <Btn kind="ghost" busy={starting} onClick={() => { const sc = detail.data?.scope; if (sc?.project_slug && !sc.task_id) void openProject(sc.project_slug, true); else nav(`/chat/${profile}`) }}>+ New chat</Btn>}
        {profile && <Btn kind="ghost" className="lg:hidden" onClick={() => setSheet(true)} aria-label="Sessions"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10" /></svg>Sessions</Btn>}
      </div>} />
      {rename && profile && <RenameDialog profile={profile} id={rename.id} initial={rename.title} onClose={() => setRename(null)} />}
      {search && <SearchModal onClose={() => setSearch(false)} />}
      {!profile && <Empty title="Pick an agent to chat with" note="Each agent talks through its own Hermes gateway; chat must be enabled on the Agents page for specialists." />}
      {profile && (
        <div className="grid min-w-0 gap-4 lg:grid-cols-[16rem_1fr]">
          <GlassCard className="hidden min-w-0 lg:flex lg:h-[calc(100dvh-12.5rem)] lg:flex-col">{sessionNav}</GlassCard>
          {sheet && (
            <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-label="Sessions" data-sheet>
              <div className="absolute inset-0 bg-bg/60" onClick={() => setSheet(false)} />
              <div className="absolute inset-x-0 bottom-0 flex max-h-[75dvh] flex-col rounded-t-2xl border border-line hq-menu p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-2xl" style={{ backdropFilter: 'blur(18px)', WebkitBackdropFilter: 'blur(18px)' }}>
                <div className="mx-auto mb-2 h-1 w-10 shrink-0 rounded-full bg-line" />
                {sessionNav}
              </div>
            </div>
          )}
          <GlassCard className="relative flex h-[calc(100dvh-15.5rem-var(--hq-kb,0px))] min-h-[14rem] min-w-0 flex-col overflow-hidden sm:h-[calc(100dvh-12.5rem-var(--hq-kb,0px))]" data-chat-card>
            {id && <div className="mb-2 flex min-w-0 items-center gap-2 text-xs">
              <button type="button" onClick={() => setRename({ id, title: detail.data?.title || '' })} className="min-w-0 truncate rounded px-1 font-medium hover:bg-raised" title="Rename session">{detail.data?.title || id}</button>
              <span className="shrink-0 font-mono text-[10px] text-muted">· {profile}</span>
              <button type="button" onClick={() => setFind(f => f ?? '')} className="ml-auto inline-flex shrink-0 items-center gap-1 rounded px-1.5 font-mono text-[10px] uppercase tracking-wider text-muted hover:bg-raised hover:text-fg" title="Find in conversation (Ctrl F)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.6-3.6" /></svg>find</button>
            </div>}
            {find !== null && id && <FindBar container={box} initial={find} onClose={() => setFind(null)} />}
            <div ref={box} data-transcript className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
              {id && detail.isLoading && <Loading rows={3} />}
              {id && detail.isError && <Empty error title="Could not load this session" note={String(detail.error)} />}
              {detail.data && <Transcript rows={detail.data.transcript} onChoose={busy ? undefined : (t) => void send(t)} />}
              {!id && !pendingUser && (
                <div className="m-auto flex w-full max-w-md flex-col items-center gap-4 text-center">
                  <div>
                    <p className="text-sm font-medium">New chat</p>
                    <p className="mt-1 text-xs text-muted">Pick who to talk to and, optionally, which project it is about.</p>
                  </div>
                  <div className="flex w-full flex-col gap-2 sm:flex-row sm:justify-center">
                    <label className="flex items-center gap-2 text-xs text-muted"><span className="w-12 text-right sm:w-auto">Agent</span>
                      <Select value={profile ?? ''} onChange={e => nav(`/chat/${e.target.value}`)} className="flex-1" aria-label="Agent for the new chat">
                        {installed.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
                      </Select>{agent && <GatewayDot g={agent.gateway} />}</label>
                    <label className="flex items-center gap-2 text-xs text-muted"><span className="w-12 text-right sm:w-auto">Project</span>
                      <Select value="" disabled={starting || busy || chatDisabled} onChange={e => void openProject(e.target.value)} className="flex-1" aria-label="Project for the new chat" title="Resume this agent's latest chat about the project, or start one seeded with its brief">
                        <option value="">{starting ? 'Opening…' : 'None — just chat'}</option>
                        {(projects.data?.projects ?? []).map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}
                      </Select></label>
                  </div>
                  <div className="flex flex-wrap justify-center gap-2">{starters.map(s => <button key={s} type="button" onClick={() => void send(s)} disabled={!agentKnown || chatDisabled} className="rounded-full border border-line bg-glass px-3 py-1 text-xs text-fg hover:bg-raised disabled:opacity-50">{s}</button>)}</div>
                </div>
              )}
              {pendingUser && <Bubble role="user"><div className="whitespace-pre-wrap break-words">{pendingUser}</div></Bubble>}
              {live && live.thinking && <Thinking text={live.thinking} live />}
              {live && live.tools.map(t => <ToolCard key={t.key} t={t} />)}
              {live && (live.text || waitingFirstToken) && <Bubble role="assistant">{live.text ? <Markdown text={live.text} /> : <span className="inline-flex items-center gap-1 text-muted"><span className="hq-dot-live inline-block size-1.5 rounded-full bg-current" />thinking…</span>}</Bubble>}
              {live?.error && <p className="text-xs text-needsyou">{live.error}</p>}
            </div>
            {!atBottom && <button type="button" onClick={scrollDown} className="absolute bottom-24 left-1/2 z-10 -translate-x-1/2 rounded-full border border-line bg-glass-strong px-3 py-1 font-mono text-[10px] uppercase tracking-wider text-fg shadow-lg hover:bg-raised">↓ {unread > 0 ? `${unread} new` : 'latest'}</button>}
            <div className="mt-3 border-t border-line pt-3">
              {liveRun ? (
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs"><span className="text-working"><span className="hq-dot-live mr-1.5 inline-block size-1.5 rounded-full bg-current" />{profile} is working in this session (run #{liveRun.run_id}{liveRun.task_id ? `, task #${liveRun.task_id}` : ''}) — chat opens when the run ends.</span>{liveRun.task_id && <Link to={`/tasks/${liveRun.task_id}`} className="rounded-full border border-working/60 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-working hover:bg-working/20">Watch log</Link>}</div>
              ) : chatDisabled ? (
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted"><span>Chat is off for <span className="font-mono text-accent-2">{profile}</span> — its gateway is not enabled.</span><ActionBtn url={`/api/agent/${profile}/gateway`} label="Enable chat" body={{ enabled: true }} confirm={`Start the ${profile} gateway? Its .env gets API_SERVER_PORT/KEY if missing.`} /></div>
              ) : (
                <div className="relative" onDragOver={e => { e.preventDefault() }} onDrop={e => { e.preventDefault(); void addFiles(e.dataTransfer.files) }}>
                  {down && <div className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-needsyou/50 bg-needsyou/10 px-3 py-1.5 text-xs text-needsyou"><span>Gateway unreachable — {down.msg}</span><span className="flex gap-2"><Btn kind="warn" onClick={down.retry}>Retry</Btn><button type="button" onClick={() => setDown(null)} className="px-1 hover:text-fg">✕</button></span></div>}
                  {restored && <p className="mb-1 text-[11px] text-muted">{restored === 'pending' ? 'This message was being sent when the page reloaded — it was not delivered. Send it again?' : 'Unsent draft restored.'}</p>}
                  {slash && slash.length > 0 && !busy && (
                    <div className="mb-1 overflow-hidden rounded-lg border border-line hq-menu text-xs shadow-lg">
                      {slash.map((c, i) => <button key={c.cmd} type="button" onMouseEnter={() => setSlashSel(i)} onClick={() => { setDraft(c.cmd + (c.args ? ' ' : '')); if (!c.args) void send(c.cmd) }} className={clsx('flex w-full items-baseline gap-2 px-3 py-1 text-left hover:bg-raised', i === slashSel && 'bg-raised')}><span className="font-mono text-accent-2">{c.cmd}</span>{c.args && <span className="font-mono text-[10px] text-muted">{c.args}</span>}<span className="min-w-0 flex-1 truncate text-muted">{c.desc}</span></button>)}
                    </div>
                  )}
                  {atts.length > 0 && <div className="mb-1.5 flex flex-wrap gap-2">{atts.map(a => (
                    <span key={a.id} className="group/att relative inline-flex items-center gap-1.5 rounded-lg border border-line bg-inset p-1 text-[11px]">
                      {a.kind === 'image' ? <img src={a.dataUrl} alt={a.name} className="h-12 w-12 rounded object-cover" /> : <span className="px-1 font-mono">📄 {a.name}</span>}
                      <button type="button" aria-label={`Remove ${a.name}`} onClick={() => setAtts(x => x.filter(y => y.id !== a.id))} className="absolute -right-1.5 -top-1.5 rounded-full border border-line bg-glass-strong px-1 text-[10px] leading-none text-muted hover:text-fg">✕</button>
                    </span>))}</div>}
                  <div className="flex items-end gap-2">
                    <input ref={fileInput} type="file" multiple accept="image/*,.md,.txt,.json,.csv,.ts,.tsx,.js,.py,.yaml,.yml,.toml,.sh,.html,.css,.sql,.log" className="hidden" onChange={e => { void addFiles(e.target.files); e.target.value = '' }} />
                    <button type="button" aria-label="Attach image or text file" title="Attach image or text file (or paste / drop)" disabled={busy || !agentKnown} onClick={() => fileInput.current?.click()} className="inline-flex h-[29px] w-[29px] shrink-0 items-center justify-center rounded-full border border-line text-muted hover:text-fg disabled:opacity-50"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m21.4 11.05-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.66 5.66l-9.2 9.2a2 2 0 0 1-2.83-2.83l8.5-8.5" /></svg></button>
                    <TextArea rows={1} value={draft} placeholder={!agentKnown ? 'Loading agent…' : busy && live?.runId ? `Steer the running turn… (Enter to send guidance without stopping)` : busy ? 'Waiting for the run to start…' : window.innerWidth < 640 ? `Message ${profile}…` : `Message ${profile}… (Enter to send, Shift+Enter for newline, / for commands)`} disabled={!agentKnown || (busy && !live?.runId)}
                      style={{ resize: 'none', maxHeight: '40vh', overflowY: 'auto', paddingTop: 3.5, paddingBottom: 3.5 }}
                      onChange={e => { setDraft(e.target.value); setRestored(null); if (profile) saveDraft(profile, id, e.target.value); e.target.style.height = 'auto'; e.target.style.height = `${e.target.scrollHeight}px` }}
                      onPaste={e => { const files = Array.from(e.clipboardData?.files ?? []); if (files.length) { e.preventDefault(); void addFiles(files) } }}
                      ref={el => { if (el) { el.style.height = 'auto'; el.style.height = draft ? `${el.scrollHeight}px` : '' } }}
                      onKeyDown={e => {
                        if (slash && slash.length > 0 && !busy) {
                          if (e.key === 'ArrowDown') { e.preventDefault(); setSlashSel(i => Math.min(slash.length - 1, i + 1)); return }
                          if (e.key === 'ArrowUp') { e.preventDefault(); setSlashSel(i => Math.max(0, i - 1)); return }
                          if (e.key === 'Tab' || (e.key === 'Enter' && draft.trim() !== slash[slashSel].cmd)) { e.preventDefault(); const c = slash[slashSel]; setDraft(c.cmd + (c.args ? ' ' : '')); if (!c.args) void send(c.cmd); return }
                        }
                        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() }
                      }} />
                    {busy ? <><Btn kind="ghost" className="shrink-0" onClick={() => void send()} disabled={!draft.trim() || !live?.runId}>Steer</Btn><Btn kind="warn" className="shrink-0" onClick={() => void stop()}>Stop</Btn></> : <Btn className="shrink-0" onClick={() => void send()} disabled={(!draft.trim() && atts.length === 0) || !agentKnown}>Send</Btn>}
                  </div>
                </div>
              )}
              {detail.data && <ContextLine d={detail.data} opts={opts} onOptions={() => setShowOpts(o => !o)} />}
              {showOpts && profile && <OptionsPanel opts={opts} current={detail.data?.model ?? null} profile={profile} onChange={setOpt} onClose={() => setShowOpts(false)} />}
            </div>
          </GlassCard>
        </div>
      )}
    </section>
  )
}
