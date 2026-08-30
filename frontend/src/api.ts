import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Human } from './status'

export class ApiError extends Error { constructor(public status: number, message: string) { super(message) } }
let csrf = ''
export const setCsrf = (t: string) => { csrf = t }
export const getCsrf = () => csrf

async function parse<T>(r: Response, url: string): Promise<T> {
  if (r.status === 401) { window.dispatchEvent(new Event('hq:unauthenticated')) }
  if (!r.ok) {
    let msg = `${r.status} ${url}`
    try { const j = await r.json(); msg = j.detail ?? j.error ?? msg } catch {}
    throw new ApiError(r.status, msg)
  }
  return r.json()
}
export async function get<T>(url: string): Promise<T> { return parse<T>(await fetch(url), url) }
export async function post<T = unknown>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', 'x-csrf': csrf }, body: body === undefined ? undefined : JSON.stringify(body) })
  return parse<T>(r, url)
}

/** Mutation that invalidates every list after a write and surfaces the engine's own message. */
export function useWrite<TBody = unknown>(url: string | ((b: TBody) => string), opts?: { onSuccess?: (d: unknown) => void }) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: TBody) => post(typeof url === 'function' ? url(body) : url, body),
    onSuccess: d => {
      const t = (d as { task?: TaskDetail } | null)?.task
      if (t && typeof t.id === 'number') qc.setQueryData(['task', t.id], t)   // task writes return the fresh task: show it now
      qc.invalidateQueries(); opts?.onSuccess?.(d)
    },
  })
}
export const useSession = () => useQuery({ queryKey: ['session'], queryFn: () => get<{ authenticated: boolean; csrf: string }>('/api/session'), retry: false })
export const useRoster = () => useQuery({ queryKey: ['roster'], queryFn: () => get<{ assignees: string[]; review_policies: string[] }>('/api/system/roster'), staleTime: Infinity })
export const useGoals = (project?: string) => useQuery({ queryKey: ['goals', project], queryFn: () => get<{ goals: Goal[] }>(`/api/goals${project ? `?project=${project}` : ''}`) })

export type Project = {
  id: number; slug: string; name: string; description: string; primary_path: string; archived: number
  tasks_total: number; tasks_done: number; runs_total: number; goals_total: number
  goals_draft: number; goals_planning: number; goals_planned: number; goals_released: number
  active_agents: string[]; last_activity: { action: string; ts: number; agent_profile: string; detail: string } | null
}
export type Goal = { id: number; project_id?: number; title: string; description: string; status: string; tasks_total: number; tasks_done: number }
export type Run = { id: number; task_id: number; agent_profile: string; session_id: string | null; status: string; started_at: number; finished_at: number | null; error: string | null; exit_code: number | null; branch: string | null; workdir: string | null; result_paths: string[] }
export type Task = {
  id: number; title: string; description: string; definition_of_done: string; status: string; assignee_profile: string | null
  project_slug: string; project_name?: string; goal_id: number | null; goal_title: string | null; goal_status?: string | null
  review_policy: string; is_code: number; summary: string | null; feedback: string | null; result_paths: string[]
  created_at: number; updated_at: number | null; human: Human
  deps: { id: number; status: string; title: string }[]; last_run?: Run | null
}
export type TaskDetail = Task & {
  dependents: { id: number; status: string; title: string }[]
  transitions: { id: number; ts: number; from_status: string | null; to_status: string; detail: string | null; run_id: number | null }[]
  runs: Run[]
  reviews: { id: number; status: string; verdict: string | null; comments: string | null; reviewer_profile: string; requested_at: number; decided_at: number | null }[]
}
export type TasksEnvelope = { tasks: Task[]; total: number; stateCounts: Record<string, number>; stateOptions: string[]; limit: number | null; offset: number }
export type ProjectDetail = Project & { goals: Goal[]; tasks: Task[]; runs: Run[]; activity: { id: number; ts: number; action: string; detail: string; agent_profile: string | null; task_id: number | null }[]; reviews: unknown[]; agents: { name: string; runs_total?: number; status?: string }[] }

export const useProjects = (archived = false) =>
  useQuery({ queryKey: ['projects', archived], queryFn: () => get<{ projects: Project[] }>(`/api/projects?archived=${archived ? 1 : 0}`) })
export const useProject = (slug: string) =>
  useQuery({ queryKey: ['project', slug], queryFn: () => get<ProjectDetail>(`/api/project/${slug}`) })
export const useTasks = (p: { project?: string; state?: string; q?: string; limit?: number; offset?: number }) => {
  const qs = new URLSearchParams()
  Object.entries(p).forEach(([k, v]) => { if (v !== undefined && v !== '' && v !== null) qs.set(k, String(v)) })
  return useQuery({ queryKey: ['tasks', p], queryFn: () => get<TasksEnvelope>(`/api/tasks?${qs}`), refetchInterval: 15000 })
}
const IN_MOTION = new Set(['ready', 'rework', 'running', 'needs_review', 'waiting_approval'])
export const useTask = (id: number) =>
  useQuery({ queryKey: ['task', id], queryFn: () => get<TaskDetail>(`/api/task/${id}`), refetchInterval: q => IN_MOTION.has((q.state.data as TaskDetail | undefined)?.status ?? '') ? 3000 : 15000 })

export function ago(ts?: number | null) {
  if (!ts) return '—'
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export const when = (ts?: number | null) => ts ? new Date(ts * 1000).toLocaleString() : '—'

export type ActivityEvent = { kind: 'activity' | 'transition'; id: number; ts: number; task_id: number | null; run_id: number | null; agent_profile: string | null; action: string; detail: string | null; project_slug: string | null; task_title: string | null }
export type Overview = { stats: { needsyou: number; working: number; queued: number; backlog: number; done_today: number; open_reviews: number; paused: boolean; cap: number; slots_used: number }; needsyou: Task[]; working: Task[]; queued: Task[]; activity: (ActivityEvent & { model?: string })[]; ts: number }
export type SystemInfo = { version: string; paused: boolean; running: number; cap: number; dispatcher: { enabled: boolean; alive: boolean; last_error: string | null }; imported_from: string | null }
export const useSystem = () => useQuery({ queryKey: ['system'], queryFn: () => get<SystemInfo>('/api/system'), refetchInterval: 15000 })
export const useOverview = () => useQuery({ queryKey: ['overview'], queryFn: () => get<Overview>('/api/overview'), refetchInterval: 10000 })
export const useActivity = (p: { project?: string; agent?: string; task_id?: number; before?: number; limit?: number }) => {
  const qs = new URLSearchParams(); Object.entries(p).forEach(([k, v]) => { if (v !== undefined && v !== '' && v !== null) qs.set(k, String(v)) })
  return useQuery({ queryKey: ['activity', p], queryFn: () => get<{ events: ActivityEvent[]; next_before: number | null }>(`/api/activity?${qs}`), refetchInterval: p.before ? false : 15000 })
}
export const useRunLog = (runId: number | null, offset: number, active: boolean) =>
  useQuery({ queryKey: ['runlog', runId, offset], enabled: runId != null, refetchInterval: active ? 3000 : false,
    queryFn: () => get<{ exists: boolean; offset: number; size: number; next: number; data: string; truncated: boolean }>(`/api/run/${runId}/log?offset=${offset}`) })

// ---- agents -------------------------------------------------------------
export type Gateway = { configured: boolean; port: number | null; enabled: boolean; running: boolean; last_used: string | null }
export type LiveRun = { run_id: number; task_id: number | null; task_title: string | null; started_at: number; session_id: string | null; review_id: number | null }
export type AgentSummary = {
  name: string; live: LiveRun[]; role?: string; installed: boolean; home: string; description: string; has_template: boolean; gateway: Gateway
  overlay_applied?: boolean; runs: number; runs_running: number; runs_done: number; runs_failed: number; last_run_at: number | null
  tasks_assigned: number; sessions: number; last_active_at: number | null; estimated_cost_usd: number | null; active_now?: boolean
}
export type AgentTemplate = { name: string; description: string; overlay: boolean; skills: string[]; installed: boolean }
export type AgentRun = { id: number; task_id: number | null; status: string; started_at: number; finished_at: number | null; error: string | null; session_id: string | null; task_title: string | null }
export type ChatScope = { project_id: number | null; project_slug: string | null; project_name: string | null; task_id: number | null; task_title: string | null }
export type AgentSession = { id: string; title: string | null; model: string | null; started_at: number | null; last_activity_at: number | null; message_count: number | null; estimated_cost_usd: number | null; source: string | null; scope?: ChatScope | null }
export type AgentRunBrief = { id: number; task_id: number | null; task_title: string | null; status: string; started_at: number; finished_at: number | null; error: string | null; review_id: number | null }
export type AgentHistoryItem = { session: AgentSession | null; run: AgentRunBrief | null; ts: number; kind: 'run' | 'chat' | 'cli' }
export type AgentDetail = AgentSummary & { history: AgentHistoryItem[] }
export const useAgents = () => useQuery({ queryKey: ['agents'], queryFn: () => get<{ agents: AgentSummary[]; templates: AgentTemplate[] }>('/api/agents'), refetchInterval: 15000 })
export const useAgent = (name: string) => useQuery({ queryKey: ['agent', name], queryFn: () => get<AgentDetail>(`/api/agent/${name}`), refetchInterval: 15000 })

// ---- chat ---------------------------------------------------------------
export type ChatMessage = { id: number; role: string; content: string | null; timestamp: number | null; tool_name: string | null; token_count: number | null; display_kind: string | null; active: number | null }
export type SessionDetail = AgentSession & { scope: ChatScope | null; live_run: { run_id: number; task_id: number | null; task_title: string | null; started_at: number } | null; usage: { model: string; input_tokens: number; output_tokens: number; estimated_cost_usd: number | null }[]; transcript: ChatMessage[] }
export const useAgentSessions = (name: string | undefined) => useQuery({ queryKey: ['agent-sessions', name], queryFn: () => get<{ sessions: AgentSession[] }>(`/api/agent/${name}/sessions?limit=100`), enabled: !!name, refetchInterval: 20000 })
export const useSessionDetail = (profile: string | undefined, id: string | undefined) => useQuery({ queryKey: ['session', profile, id], queryFn: () => get<SessionDetail>(`/api/session/${profile}/${id}`), enabled: !!profile && !!id, retry: false })

export type SseEvent = { name: string; data: Record<string, unknown> }
export type ScopedSession = { id: number; profile: string; session_id: string; project_id: number | null; task_id: number | null; title: string | null; created_at: number; project_slug: string | null; project_name: string | null; task_title: string | null }
export const useScopedSessions = (kind: 'project' | 'task', key: string | number | undefined) =>
  useQuery({ queryKey: ['chat-scoped', kind, key], queryFn: () => get<{ sessions: ScopedSession[] }>(`/api/${kind}/${key}/chat-sessions`), enabled: key !== undefined && key !== '' })
/** Create a project/task-linked session; the caller streams `brief` as the first visible turn. */
export const startScopedChat = (body: { profile: string; project_id?: number; task_id?: number }) => post<{ id: string; profile: string; title: string; brief: string; scope: ChatScope }>('/api/chat/start', body)

/** POST a chat message and stream the gateway's SSE events back. Resolves when the stream ends. */
export async function streamChat(profile: string, sessionId: string, message: string, onEvent: (e: SseEvent) => void, signal?: AbortSignal): Promise<void> {
  const r = await fetch(`/api/chat/${profile}/${sessionId}`, { method: 'POST', headers: { 'content-type': 'application/json', 'x-csrf': csrf }, body: JSON.stringify({ message }), signal })
  if (!r.ok || !r.body) { let msg = `${r.status}`; try { msg = (await r.json()).detail ?? msg } catch {} throw new ApiError(r.status, msg) }
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2)
      let name = 'message', data: Record<string, unknown> = {}
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) name = line.slice(7)
        else if (line.startsWith('data: ')) { try { data = JSON.parse(line.slice(6)) } catch { data = { raw: line.slice(6) } } }
      }
      onEvent({ name, data })
    }
  }
}
