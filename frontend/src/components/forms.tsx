import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useGoals, useProjects, useRoster, useTasks, useWrite, ApiError } from '../api'
import { Modal, Field, TextInput, TextArea, SelectInput, Btn } from './Modal'
import { useToast } from './Toast'

const errText = (e: unknown) => e instanceof ApiError ? e.message : String(e)

export function NewProjectModal({ onClose }: { onClose: () => void }) {
  const toast = useToast(); const nav = useNavigate()
  const [f, setF] = useState({ slug: '', name: '', description: '', primary_path: '' })
  const m = useWrite('/api/projects', { onSuccess: (d) => { toast(`Project created: ${(d as { slug: string }).slug}`); onClose(); nav(`/projects/${(d as { slug: string }).slug}`) } })
  const slugify = (v: string) => v.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return (
    <Modal title="New project" onClose={onClose}>
      <form className="flex flex-col gap-3" onSubmit={e => { e.preventDefault(); m.mutate(f, { onError: x => toast(errText(x), 'err') }) }}>
        <Field label="Name"><TextInput required value={f.name} onChange={e => setF({ ...f, name: e.target.value, slug: f.slug || slugify(e.target.value) })} /></Field>
        <Field label="Slug" hint="lowercase, dashes; becomes the folder name"><TextInput required pattern="[a-z0-9][a-z0-9-]*" value={f.slug} onChange={e => setF({ ...f, slug: e.target.value })} /></Field>
        <Field label="Description"><TextArea value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Path" hint="leave empty to create <projects root>/<slug>"><TextInput value={f.primary_path} onChange={e => setF({ ...f, primary_path: e.target.value })} placeholder="/opt/data/projects/…" /></Field>
        <div className="mt-2 flex justify-end gap-2"><Btn kind="ghost" type="button" onClick={onClose}>Cancel</Btn><Btn type="submit" busy={m.isPending}>Create</Btn></div>
      </form>
    </Modal>
  )
}

export function NewTaskModal({ project, onClose }: { project?: string; onClose: () => void }) {
  const toast = useToast(); const nav = useNavigate()
  const projects = useProjects(); const roster = useRoster()
  const [f, setF] = useState({ project: project ?? '', title: '', description: '', definition_of_done: '', assignee: '', goal_id: '', review_policy: 'none', is_code: false, owner_approval: false, phased: false, deps: '' })
  const goals = useGoals(f.project || undefined)
  const candidates = useTasks({ project: f.project || undefined, limit: 200 })
  const m = useWrite('/api/tasks', { onSuccess: (d) => { const x = d as { id: number; build_id?: number }; toast(x.build_id ? `Phased tasks #${x.id} and #${x.build_id} created` : `Task #${x.id} created (backlog — mark ready to queue it)`); onClose(); nav(`/tasks/${x.id}`) } })
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    m.mutate({ ...f, assignee: f.assignee || null, goal_id: f.goal_id ? Number(f.goal_id) : null,
      deps: f.deps.split(/[\s,#]+/).filter(Boolean).map(Number).filter(n => !isNaN(n)) }, { onError: x => toast(errText(x), 'err') })
  }
  return (
    <Modal title="New task" onClose={onClose}>
      <form className="flex flex-col gap-3" onSubmit={submit}>
        <Field label="Project"><SelectInput required value={f.project} onChange={e => setF({ ...f, project: e.target.value, goal_id: '' })}>
          <option value="">Choose…</option>{(projects.data?.projects ?? []).map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}</SelectInput></Field>
        <Field label="Title"><TextInput required autoFocus value={f.title} onChange={e => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Description"><TextArea value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Definition of done"><TextArea rows={2} value={f.definition_of_done} onChange={e => setF({ ...f, definition_of_done: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Assignee"><SelectInput value={f.assignee} onChange={e => setF({ ...f, assignee: e.target.value })}><option value="">unassigned</option>{(roster.data?.assignees ?? []).map(a => <option key={a}>{a}</option>)}</SelectInput></Field>
          <Field label="Review"><SelectInput value={f.review_policy} onChange={e => setF({ ...f, review_policy: e.target.value })}>{(roster.data?.review_policies ?? ['none']).map(a => <option key={a}>{a}</option>)}</SelectInput></Field>
          <Field label="Goal"><SelectInput value={f.goal_id} onChange={e => setF({ ...f, goal_id: e.target.value })}><option value="">none</option>{(goals.data?.goals ?? []).map(g => <option key={g.id} value={g.id}>#{g.id} {g.title} ({g.status})</option>)}</SelectInput></Field>
          <Field label="Depends on" hint="task ids, e.g. 81, 83"><TextInput value={f.deps} onChange={e => setF({ ...f, deps: e.target.value })} list="hq-tasks" /><datalist id="hq-tasks">{(candidates.data?.tasks ?? []).map(t => <option key={t.id} value={t.id}>{t.title}</option>)}</datalist></Field>
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.is_code} onChange={e => setF({ ...f, is_code: e.target.checked })} /> Code task (runs in an isolated git worktree)</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.owner_approval} onChange={e => setF({ ...f, owner_approval: e.target.checked })} /> Owner approval required</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.phased} onChange={e => setF({ ...f, phased: e.target.checked })} /> Phased (plan then build)</label>
        <div className="mt-2 flex justify-end gap-2"><Btn kind="ghost" type="button" onClick={onClose}>Cancel</Btn><Btn type="submit" busy={m.isPending}>Create</Btn></div>
      </form>
    </Modal>
  )
}

export function NewGoalModal({ project, onClose }: { project: string; onClose: () => void }) {
  const toast = useToast()
  const [f, setF] = useState({ project, title: '', description: '', acceptance_criteria: '' })
  const m = useWrite('/api/goals', { onSuccess: (d) => { toast(`Goal #${(d as { id: number }).id} created as draft`); onClose() } })
  return (
    <Modal title="New goal" onClose={onClose}>
      <form className="flex flex-col gap-3" onSubmit={e => { e.preventDefault(); m.mutate(f, { onError: x => toast(errText(x), 'err') }) }}>
        <Field label="Title"><TextInput required autoFocus value={f.title} onChange={e => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Description"><TextArea value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Acceptance criteria"><TextArea rows={2} value={f.acceptance_criteria} onChange={e => setF({ ...f, acceptance_criteria: e.target.value })} /></Field>
        <div className="mt-2 flex justify-end gap-2"><Btn kind="ghost" type="button" onClick={onClose}>Cancel</Btn><Btn type="submit" busy={m.isPending}>Create</Btn></div>
      </form>
    </Modal>
  )
}

export function FeedbackModal({ taskId, onClose }: { taskId: number; onClose: () => void }) {
  const toast = useToast()
  const [comment, setComment] = useState('')
  const looksLikeApproval = /\b(?:approved|ok(?:ay)?|yes|go ahead|proceed)\b/i.test(comment)
  const m = useWrite(`/api/task/${taskId}/feedback`, { onSuccess: () => { toast(`Reply sent — task #${taskId} goes back to the agent as rework`); onClose() } })
  return (
    <Modal title={`Reply to task #${taskId}`} onClose={onClose}>
      <p className="mb-3 text-xs text-muted">Your comment is threaded into the next run's brief as OWNER FEEDBACK; the task becomes rework and is re-queued.</p>
      <form className="flex flex-col gap-3" onSubmit={e => { e.preventDefault(); m.mutate({ comment }, { onError: x => toast(errText(x), 'err') }) }}>
        <TextArea autoFocus rows={5} required value={comment} onChange={e => setComment(e.target.value)} placeholder="Answer the agent's question, or say what to change…" />
        {looksLikeApproval && <p role="alert" className="text-xs text-needsyou">Feedback sends this task BACK for rework — to approve, use the Approve button.</p>}
        <div className="flex justify-end gap-2"><Btn kind="ghost" type="button" onClick={onClose}>Cancel</Btn><Btn type="submit" busy={m.isPending} disabled={!comment.trim()}>Send & rework</Btn></div>
      </form>
    </Modal>
  )
}

/** Confirmed one-shot action button. */
export function ActionBtn({ url, label, confirm, kind = 'primary', onDone, body }: { url: string; label: string; confirm?: string; kind?: 'primary' | 'ghost' | 'warn'; onDone?: (d: unknown) => void; body?: unknown }) {
  const toast = useToast()
  const m = useWrite(url, { onSuccess: (d) => { toast(`${label}: done`); onDone?.(d) } })
  return <Btn kind={kind} busy={m.isPending} onClick={() => { if (!confirm || window.confirm(confirm)) m.mutate(body, { onError: x => toast(errText(x), 'err') }) }}>{label}</Btn>
}
