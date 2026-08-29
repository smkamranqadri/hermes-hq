import { useQuery } from '@tanstack/react-query'
import type { Human } from './status'

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

export type Project = {
  id: number; slug: string; name: string; description: string; primary_path: string; archived: number
  tasks_total: number; tasks_done: number; runs_total: number; goals_total: number
  goals_draft: number; goals_planning: number; goals_planned: number; goals_released: number
  active_agents: string[]; last_activity: { action: string; ts: number; agent_profile: string; detail: string } | null
}
export type Goal = { id: number; title: string; description: string; status: string; tasks_total: number; tasks_done: number }
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
export const useTask = (id: number) =>
  useQuery({ queryKey: ['task', id], queryFn: () => get<TaskDetail>(`/api/task/${id}`), refetchInterval: 10000 })

export function ago(ts?: number | null) {
  if (!ts) return '—'
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export const when = (ts?: number | null) => ts ? new Date(ts * 1000).toLocaleString() : '—'
