import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { useAgents, useAgentSessions, useSessionDetail, useProjects, startScopedChat, streamChat, post, ago, ApiError, type SseEvent, type ChatMessage } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Loading, Chip, Select, Label } from '../components/ui'
import { ActionBtn } from '../components/forms'
import { Btn, TextArea } from '../components/Modal'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'
import { GatewayDot } from './Agents'

type LiveTool = { key: string; name: string; state: 'started' | 'completed' | 'failed'; preview?: string }
type Live = { text: string; tools: LiveTool[]; thinking: string; runId: string | null; error: string | null }
const emptyLive = (): Live => ({ text: '', tools: [], thinking: '', runId: null, error: null })

function Bubble({ role, children, tool }: { role: string; children: React.ReactNode; tool?: boolean }) {
  const mine = role === 'user'
  return (
    <div className={clsx('flex min-w-0', mine ? 'justify-end' : 'justify-start')}>
      <div className={clsx('min-w-0 max-w-[92%] rounded-2xl px-3.5 py-2 text-sm sm:max-w-[80%]', mine ? 'bg-accent/20 text-fg' : tool ? 'border border-line bg-inset font-mono text-[11px] text-muted' : 'bg-raised text-fg')}>{children}</div>
    </div>
  )
}

function ScopeChip({ scope, className }: { scope: { project_slug: string | null; project_name: string | null; task_id: number | null; task_title: string | null } | null | undefined; className?: string }) {
  if (!scope) return null
  const to = scope.task_id ? `/tasks/${scope.task_id}` : `/projects/${scope.project_slug}`
  const label = scope.task_id ? `Task #${scope.task_id}` : `Project ${scope.project_name ?? scope.project_slug}`
  return <Link to={to} title={scope.task_title ?? scope.project_name ?? ''} onClick={e => e.stopPropagation()} className={clsx('inline-flex shrink-0 items-center rounded-full border border-accent/50 bg-accent/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-accent-2 hover:bg-accent/20', className)}>{label}</Link>
}

function Transcript({ rows }: { rows: ChatMessage[] }) {
  return <>{rows.filter(m => m.role !== 'system').map(m => {
    if (m.role === 'tool' || m.tool_name) return <Bubble key={m.id} role="tool" tool><span className="text-accent-2">{m.tool_name ?? 'tool'}</span> {(m.content ?? '').slice(0, 300)}</Bubble>
    return <Bubble key={m.id} role={m.role}><div className="whitespace-pre-wrap break-words">{m.content}</div></Bubble>
  })}</>
}

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
  useEffect(() => {
    const el = box.current; if (!el) return
    const down = () => { el.scrollTop = el.scrollHeight }
    down(); const raf = requestAnimationFrame(down); const t = setTimeout(down, 200)
    const ro = new ResizeObserver(down); ro.observe(el); Array.from(el.children).forEach(c => ro.observe(c))
    return () => { cancelAnimationFrame(raf); clearTimeout(t); ro.disconnect() }
  }, [detail.data?.transcript.length, live?.text, live?.tools.length, pendingUser, id])
  const installed = useMemo(() => (agents.data?.agents ?? []).filter(a => a.installed), [agents.data])
  const busy = live !== null

  async function send(text?: string) {
    const msg = (text ?? draft).trim()
    if (!profile || !msg || busy) return
    let sid = id
    try {
      if (!sid) { const s = await post<{ id: string }>(`/api/chat/${profile}/sessions`, { title: msg.slice(0, 60) }); sid = s.id; nav(`/chat/${profile}/${sid}`, { replace: true }) }
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err'); return }
    if (text === undefined) setDraft('')
    setPendingUser(msg); setLive(emptyLive())
    let failed = false
    abort.current = new AbortController()
    const onEvent = (e: SseEvent) => setLive(l => {
      if (!l) return l
      const d = e.data as Record<string, string | undefined>
      switch (e.name) {
        case 'run.started': return { ...l, runId: (d.run_id as string) ?? l.runId }
        case 'assistant.delta': return { ...l, text: l.text + (d.delta ?? ''), thinking: '' }
        case 'tool.progress': return { ...l, thinking: (l.thinking + (d.delta ?? '')).slice(-400) }
        case 'tool.started': return { ...l, tools: [...l.tools, { key: `${l.tools.length}`, name: d.tool_name ?? 'tool', state: 'started', preview: d.preview }] }
        case 'tool.completed': case 'tool.failed': {
          const tools = [...l.tools]; const i = tools.map(t => t.state).lastIndexOf('started')
          if (i >= 0) tools[i] = { ...tools[i], state: e.name === 'tool.failed' ? 'failed' : 'completed', preview: d.preview ?? tools[i].preview }
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
  /** Project picker: start a NEW session for this agent seeded with the project brief. */
  async function startProject(slug: string) {
    const p = projects.data?.projects.find(x => x.slug === slug)
    if (!profile || !p || starting || busy) return
    setStarting(true)
    try { const s = await startScopedChat({ profile, project_id: p.id }); qc.invalidateQueries({ queryKey: ['agent-sessions', profile] }); nav(`/chat/${s.profile}/${s.id}`, { state: { seed: s.brief } }) }
    catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err') }
    finally { setStarting(false) }
  }
  async function stop() {
    if (!profile || !id || !live) return
    if (live.runId) { try { await post(`/api/chat/${profile}/${id}/stop/${live.runId}`) } catch (e) { toast(e instanceof ApiError ? e.message : String(e), 'err') } }
    abort.current?.abort()
  }

  const agentKnown = !!agent
  const chatDisabled = agentKnown && agent.name !== 'orchestrator' && !agent.gateway.enabled
  const liveRun = detail.data?.live_run ?? null
  return (
    <section className="mx-auto flex max-w-6xl flex-col p-4 sm:p-6">
      <PageHeader crumb="chat" title="Chat" right={<div className="flex flex-wrap items-center gap-2">
        <Select value={profile ?? ''} onChange={e => nav(e.target.value ? `/chat/${e.target.value}` : '/chat')}>
          <option value="">Pick an agent…</option>
          {installed.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
        </Select>
        {profile && <Select value="" disabled={starting || busy} onChange={e => void startProject(e.target.value)} title="Start a new chat seeded with a project brief">
          <option value="">{starting ? 'Starting…' : 'New chat about a project…'}</option>
          {(projects.data?.projects ?? []).map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}
        </Select>}
        {profile && <Select value={id ?? ''} onChange={e => nav(e.target.value ? `/chat/${profile}/${e.target.value}` : `/chat/${profile}`)} className="max-w-[46vw] sm:max-w-xs lg:hidden">
          <option value="">New session</option>
          {id && !(sessions.data?.sessions ?? []).some(s => s.id === id) && <option value={id}>{id}</option>}
          {(sessions.data?.sessions ?? []).map(s => <option key={s.id} value={s.id}>{s.scope ? (s.scope.task_id ? `[#${s.scope.task_id}] ` : `[${s.scope.project_slug}] `) : ''}{s.title || s.id}</option>)}
        </Select>}
      </div>} />
      {!profile && <Empty title="Pick an agent to chat with" note="Each agent talks through its own Hermes gateway; chat must be enabled on the Agents page for specialists." />}
      {profile && (
        <div className="grid min-w-0 gap-4 lg:grid-cols-[16rem_1fr]">
          <GlassCard className="hidden min-w-0 lg:flex lg:h-[calc(100dvh-12.5rem)] lg:flex-col">
            <div className="flex items-center justify-between"><Label>Sessions</Label>{agent && <GatewayDot g={agent.gateway} />}</div>
            <Link to={`/chat/${profile}`} className={clsx('mt-2 block rounded-lg px-2 py-1.5 text-xs hover:bg-raised', !id && 'bg-raised')}>+ New session</Link>
            {sessions.isLoading && <Loading rows={4} />}
            <ul className="mt-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
              {(sessions.data?.sessions ?? []).map(s => (
                <li key={s.id}><Link to={`/chat/${profile}/${s.id}`} className={clsx('block truncate rounded-lg px-2 py-1.5 text-xs hover:bg-raised', s.id === id && 'bg-raised')} title={s.id}>{s.scope && <span className="mr-1 font-mono text-[10px] text-accent-2">{s.scope.task_id ? `#${s.scope.task_id}` : s.scope.project_slug}</span>}{s.title || s.id}<span className="ml-1 text-[10px] text-muted">{ago(s.last_activity_at ?? s.started_at)}</span></Link></li>
              ))}
            </ul>
          </GlassCard>
          <GlassCard className="flex h-[calc(100dvh-15.5rem)] min-h-[22rem] min-w-0 flex-col overflow-hidden sm:h-[calc(100dvh-12.5rem)]">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
              <span className="font-mono text-accent-2">{profile}</span>{agent && <GatewayDot g={agent.gateway} />}
              <ScopeChip scope={detail.data?.scope} />
              {id && <span className="truncate font-mono text-[10px]">{id}</span>}
              {detail.data?.model && <Chip>{detail.data.model}</Chip>}
            </div>
            <div ref={box} data-transcript className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
              {id && detail.isLoading && <Loading rows={3} />}
              {id && detail.isError && <Empty error title="Could not load this session" note={String(detail.error)} />}
              {detail.data && <Transcript rows={detail.data.transcript} />}
              {!id && !pendingUser && <p className="m-auto text-center text-xs text-muted">New session with <span className="font-mono text-accent-2">{profile}</span>. Say something.</p>}
              {pendingUser && <Bubble role="user"><div className="whitespace-pre-wrap break-words">{pendingUser}</div></Bubble>}
              {live && live.tools.map(t => <Bubble key={t.key} role="tool" tool><span className={clsx(t.state === 'failed' ? 'text-needsyou' : 'text-accent-2')}>{t.name}</span> {t.preview ?? ''} <span className="text-muted">· {t.state}</span></Bubble>)}
              {live && live.thinking && <Bubble role="tool" tool><span className="italic">thinking…</span> {live.thinking.slice(-160)}</Bubble>}
              {live && (live.text || live.tools.length === 0) && <Bubble role="assistant"><div className="whitespace-pre-wrap break-words">{live.text || <span className="animate-pulse text-muted">…</span>}</div></Bubble>}
              {live?.error && <p className="text-xs text-needsyou">{live.error}</p>}
            </div>
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
            </div>
          </GlassCard>
        </div>
      )}
    </section>
  )
}
