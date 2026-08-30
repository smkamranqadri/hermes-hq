import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { useAgents, useAgentSessions, useSessionDetail, useProjects, startScopedChat, streamChat, post, get, ago, when, ApiError, type SseEvent, type ChatMessage, type ScopedSession, type SessionDetail } from '../api'
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
function Transcript({ rows }: { rows: ChatMessage[] }) {
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
      if (m.content && m.content.trim()) out.push(<Bubble key={m.id} role={m.role} ts={m.timestamp} tokens={m.token_count}>{m.role === 'assistant' ? <Markdown text={m.content} /> : <div className="whitespace-pre-wrap break-words">{m.content}</div>}</Bubble>)
    }
    return out
  }, [rows])
  return <>{items}</>
}

function ScopeChip({ scope, className }: { scope: { project_slug: string | null; project_name: string | null; task_id: number | null; task_title: string | null } | null | undefined; className?: string }) {
  if (!scope) return null
  const to = scope.task_id ? `/tasks/${scope.task_id}` : `/projects/${scope.project_slug}`
  const label = scope.task_id ? `Task #${scope.task_id}` : `Project ${scope.project_name ?? scope.project_slug}`
  return <Link to={to} title={scope.task_title ?? scope.project_name ?? ''} onClick={e => e.stopPropagation()} className={clsx('inline-flex shrink-0 items-center rounded-full border border-accent/50 bg-accent/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-accent-2 hover:bg-accent/20', className)}>{label}</Link>
}

/** Status line under the composer, shaped like a terminal statusline: model · ██░░ pct · window · cost · scope. Click for the breakdown. */
function ContextLine({ d }: { d: SessionDetail }) {
  const [open, setOpen] = useState(false)
  const cost = d.actual_cost_usd || d.estimated_cost_usd
  const est = d.cost_estimate
  const c = d.context
  const pct = c?.pct ?? null
  const tone = pct == null ? 'text-muted' : pct >= 80 ? 'text-needsyou' : pct >= 50 ? 'text-queued' : 'text-working'
  const filled = pct == null ? 0 : Math.min(10, Math.round(pct / 10))
  const bar = '█'.repeat(filled) + '░'.repeat(10 - filled)
  const scope = d.scope?.task_id ? `#${d.scope.task_id}` : d.scope?.project_slug
  if (!d.model && !(c && c.used > 0)) return null
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 font-mono text-[10px] text-muted">
      <button type="button" onClick={() => setOpen(o => !o)} className="inline-flex flex-wrap items-center gap-x-3 hover:text-fg" title={c ? `context ≈ ${c.used.toLocaleString()} of ${c.limit ? c.limit.toLocaleString() : '?'} tokens — transcript ${c.transcript.toLocaleString()} + system overhead ${c.overhead.toLocaleString()} (${c.source}); click for the breakdown` : 'click for the breakdown'}>
        {d.model && <span className="text-accent-2">{d.model}</span>}
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

  async function send(text?: string) {
    const msg = (text ?? draft).trim()
    if (!profile || !msg || busy) return
    let sid = id
    try {
      if (!sid) { const s = await post<{ id: string }>(`/api/chat/${profile}/sessions`, { title: msg.slice(0, 60) }); sid = s.id; nav(`/chat/${profile}/${sid}`, { replace: true }) }
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err'); return }
    if (text === undefined) setDraft('')
    setPendingUser(msg); setLive(emptyLive()); setAtBottom(true)
    let failed = false
    abort.current = new AbortController()
    const onEvent = (e: SseEvent) => setLive(l => {
      if (!l) return l
      const d = e.data as Record<string, string | undefined>
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
    try { await streamChat(profile, sid!, msg, onEvent, abort.current.signal) }
    catch (e) { if (!(e instanceof DOMException && e.name === 'AbortError')) { failed = true; toast(e instanceof ApiError ? e.message : String(e), 'err') } }
    finally {
      abort.current = null; setLive(null); setPendingUser(null)
      if (failed) setDraft(msg)
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
  return (
    <section className="mx-auto flex max-w-6xl flex-col p-4 sm:p-6">
      <PageHeader crumb="chat" title="Chat" right={<div className="flex flex-wrap items-center gap-2">
        {id && <ScopeChip scope={detail.data?.scope} />}
        {id && <Btn kind="ghost" busy={starting} onClick={() => { const sc = detail.data?.scope; if (sc?.project_slug && !sc.task_id) void openProject(sc.project_slug, true); else nav(`/chat/${profile}`) }}>+ New chat</Btn>}
        {profile && <Select value={id ?? ''} onChange={e => nav(e.target.value ? `/chat/${profile}/${e.target.value}` : `/chat/${profile}`)} className="max-w-[46vw] sm:max-w-xs lg:hidden">
          <option value="">New session</option>
          {id && !(sessions.data?.sessions ?? []).some(s => s.id === id) && <option value={id}>{id}</option>}
          {(sessions.data?.sessions ?? []).map(s => <option key={s.id} value={s.id}>{s.scope ? (s.scope.task_id ? `[#${s.scope.task_id}] ` : `[${s.scope.project_slug}] `) : ''}{s.title || s.id}</option>)}
        </Select>}
      </div>} />
      {rename && profile && <RenameDialog profile={profile} id={rename.id} initial={rename.title} onClose={() => setRename(null)} />}
      {search && <SearchModal onClose={() => setSearch(false)} />}
      {!profile && <Empty title="Pick an agent to chat with" note="Each agent talks through its own Hermes gateway; chat must be enabled on the Agents page for specialists." />}
      {profile && (
        <div className="grid min-w-0 gap-4 lg:grid-cols-[16rem_1fr]">
          <GlassCard className="hidden min-w-0 lg:flex lg:h-[calc(100dvh-12.5rem)] lg:flex-col">
            <div className="flex items-center justify-between gap-2">
              <Select value={profile ?? ''} onChange={e => nav(e.target.value ? `/chat/${e.target.value}` : '/chat')} className="min-w-0 max-w-[9rem]" aria-label="Agent">
                {installed.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
              </Select>
              {agent && <GatewayDot g={agent.gateway} />}
            </div>
            <Link to={`/chat/${profile}`} className={clsx('mt-2 block rounded-lg px-2 py-1.5 text-xs hover:bg-raised', !id && 'bg-raised')}>+ New session</Link>
            {sessions.isLoading && <Loading rows={4} />}
            <ul className="mt-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
              {(() => { const all = sessions.data?.sessions ?? []; const pinned = all.filter(s => s.pinned); const rest = all.filter(s => !s.pinned)
                const row = (s: typeof all[number]) => (
                  <li key={s.id} className="group/sess flex min-w-0 items-center gap-1">
                    <Link to={`/chat/${profile}/${s.id}`} className={clsx('block min-w-0 flex-1 truncate rounded-lg px-2 py-1.5 text-xs hover:bg-raised', s.id === id && 'bg-raised')} title={s.id}>{s.pinned ? <span className="mr-1 text-[10px] text-muted">📌</span> : null}{s.scope && <span className="mr-1 font-mono text-[10px] text-accent-2">{s.scope.task_id ? `#${s.scope.task_id}` : s.scope.project_slug}</span>}{s.title || s.id}<span className="ml-1 text-[10px] text-muted">{ago(s.last_activity_at ?? s.started_at)}</span></Link>
                    <SessionMenu profile={profile} s={s} current={s.id === id} onRename={() => setRename({ id: s.id, title: s.title || '' })} />
                  </li>)
                return <>{pinned.length > 0 && <li className="px-2 pt-1 font-mono text-[10px] uppercase tracking-wider text-muted">Pinned</li>}{pinned.map(row)}{pinned.length > 0 && rest.length > 0 && <li className="px-2 pt-2 font-mono text-[10px] uppercase tracking-wider text-muted">Recent</li>}{rest.map(row)}</>
              })()}
            </ul>
            <button type="button" onClick={() => setSearch(true)} className="mt-2 flex items-center justify-between rounded-lg border border-line px-2 py-1 text-[11px] text-muted hover:bg-raised hover:text-fg"><span>Search all chats</span><kbd className="font-mono text-[10px]">Ctrl K</kbd></button>
          </GlassCard>
          <GlassCard className="relative flex h-[calc(100dvh-15.5rem)] min-h-[22rem] min-w-0 flex-col overflow-hidden sm:h-[calc(100dvh-12.5rem)]">
            {id && <div className="mb-2 flex min-w-0 items-center gap-2 text-xs">
              <button type="button" onClick={() => setRename({ id, title: detail.data?.title || '' })} className="min-w-0 truncate rounded px-1 font-medium hover:bg-raised" title="Rename session">{detail.data?.title || id}</button>
              <span className="shrink-0 font-mono text-[10px] text-muted">· {profile}</span>
              <button type="button" onClick={() => setFind(f => f ?? '')} className="ml-auto shrink-0 rounded px-1.5 font-mono text-[10px] uppercase tracking-wider text-muted hover:bg-raised hover:text-fg" title="Find in conversation (Ctrl F)">find</button>
            </div>}
            {find !== null && id && <FindBar container={box} initial={find} onClose={() => setFind(null)} />}
            <div ref={box} data-transcript className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
              {id && detail.isLoading && <Loading rows={3} />}
              {id && detail.isError && <Empty error title="Could not load this session" note={String(detail.error)} />}
              {detail.data && <Transcript rows={detail.data.transcript} />}
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
                <div className="flex items-end gap-2">
                  <TextArea rows={2} value={draft} placeholder={agentKnown ? `Message ${profile}… (Enter to send, Shift+Enter for newline)` : 'Loading agent…'} onChange={e => setDraft(e.target.value)} disabled={busy || !agentKnown}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }} />
                  {busy ? <Btn kind="warn" onClick={() => void stop()}>Stop</Btn> : <Btn onClick={() => void send()} disabled={!draft.trim() || !agentKnown}>Send</Btn>}
                </div>
              )}
              {detail.data && <ContextLine d={detail.data} />}
            </div>
          </GlassCard>
        </div>
      )}
    </section>
  )
}
