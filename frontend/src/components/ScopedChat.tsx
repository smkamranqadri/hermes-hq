import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useAgents, useScopedSessions, startScopedChat, ago, ApiError } from '../api'
import { GlassCard } from './GlassCard'
import { Label, Loading, Select, Agent } from './ui'
import { Btn } from './Modal'
import { useToast } from './Toast'

/** "Chat about this project/task" card: agent picker (orchestrator preselected), New chat, linked sessions → Resume.
 *  New chat creates the session server-side, then hands the brief to the Chat page (router state) to stream as the first turn. */
export function ScopedChat({ kind, id, slug }: { kind: 'project' | 'task'; id: number; slug?: string }) {
  const nav = useNavigate(); const toast = useToast(); const qc = useQueryClient()
  const agents = useAgents()
  const installed = useMemo(() => (agents.data?.agents ?? []).filter(a => a.installed), [agents.data])
  const [profile, setProfile] = useState('orchestrator')
  const [busy, setBusy] = useState(false)
  const sessions = useScopedSessions(kind, kind === 'project' ? slug : id)
  const noun = kind === 'project' ? 'project' : 'task'
  const last = sessions.data?.sessions.find(s => s.profile === profile && (kind === 'task' || !s.task_id))
  async function start() {
    if (busy) return
    setBusy(true)
    try {
      const s = await startScopedChat(kind === 'project' ? { profile, project_id: id } : { profile, task_id: id })
      qc.invalidateQueries({ queryKey: ['chat-scoped'] }); qc.invalidateQueries({ queryKey: ['agent-sessions', profile] })
      nav(`/chat/${s.profile}/${s.id}`, { state: { seed: s.brief } })
    } catch (e) {
      toast(e instanceof ApiError ? e.message : String(e), 'err')
    } finally { setBusy(false) }
  }
  return (
    <GlassCard className="text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>Chat about this {noun}</Label>
        <div className="flex items-center gap-2">
          <Select value={profile} onChange={e => setProfile(e.target.value)} disabled={busy} aria-label="Agent">
            {!installed.some(a => a.name === 'orchestrator') && <option value="orchestrator">orchestrator</option>}
            {installed.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
          </Select>
          {last && <Btn onClick={() => nav(`/chat/${last.profile}/${last.session_id}`)}>Resume</Btn>}
          <Btn kind={last ? 'ghost' : 'primary'} busy={busy} onClick={() => void start()}>New chat</Btn>
        </div>
      </div>
      <p className="mt-1 text-muted">{last ? <>Resume continues <span className="font-mono text-accent-2">{profile}</span>'s latest chat about this {noun}; </>: null}New chat opens a session seeded with the {noun} brief; the agent only talks, it does not start work.</p>
      {sessions.isLoading && <div className="mt-2"><Loading rows={2} /></div>}
      {sessions.data && sessions.data.sessions.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {sessions.data.sessions.map(s => (
            <li key={s.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-line-subtle px-2 py-1.5">
              <Agent name={s.profile} />
              <span className="min-w-0 flex-1 truncate" title={s.session_id}>{s.title || s.session_id}{kind === 'project' && s.task_id ? <Link to={`/tasks/${s.task_id}`} className="ml-1 font-mono text-[10px] text-muted hover:text-accent-2">task #{s.task_id}</Link> : null}</span>
              <span className="font-mono text-[10px] text-muted">{ago(s.created_at)}</span>
              <Link to={`/chat/${s.profile}/${s.session_id}`} className="rounded-full border border-line px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider hover:bg-raised">Resume</Link>
            </li>))}
        </ul>
      )}
      {sessions.data && sessions.data.sessions.length === 0 && <p className="mt-2 text-muted">No chats about this {noun} yet.</p>}
    </GlassCard>
  )
}
