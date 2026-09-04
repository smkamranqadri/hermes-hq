import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { useProject, useProjectNotes, useAreas, ago, when } from '../api'
import { NoteRow } from '../components/brain'
import { GlassCard } from '../components/GlassCard'
import { TaskRow } from '../components/TaskRow'
import { Empty, Loading, Chip, Crumbs, Label, Agent } from '../components/ui'
import { usePageTitle } from '../usePageTitle'
import { NewTaskModal, NewGoalModal, ActionBtn } from '../components/forms'
import { Btn } from '../components/Modal'
import { ScopedChat } from '../components/ScopedChat'

const TABS = ['tasks', 'goals', 'notes', 'runs', 'activity'] as const
const GOAL_TONE: Record<string, string> = { draft: 'text-muted', planning: 'text-needsyou', planned: 'text-queued', released: 'text-working' }

export function ProjectDetail() {
  const { slug = '' } = useParams()
  const q = useProject(slug)
  const notes = useProjectNotes(slug)
  const areas = useAreas()
  const p = q.data
  usePageTitle(p?.name ?? slug)
  const [tab, setTab] = useState<(typeof TABS)[number]>('tasks')
  const [modal, setModal] = useState<'task' | 'goal' | null>(null)
  if (q.isLoading) return <section className="mx-auto max-w-6xl p-4 sm:p-6"><Loading rows={1} /><div className="mt-4"><Loading rows={4} /></div></section>
  if (q.isError || !p) return <section className="mx-auto max-w-6xl p-6"><Empty error title={`Could not load /api/project/${slug}`} note={String(q.error ?? '404')} /></section>
  const pct = p.tasks_total ? Math.round(100 * p.tasks_done / p.tasks_total) : 0
  const stuck = p.tasks.filter(t => t.human?.state === 'needsyou')
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <Crumbs items={[['Projects', '/projects'], [p.name]]} />
      {modal === 'task' && <NewTaskModal project={slug} onClose={() => setModal(null)} />}
      {modal === 'goal' && <NewGoalModal project={slug} onClose={() => setModal(null)} />}
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="break-words text-xl font-semibold tracking-tight">{p.name} {!!p.archived && <Chip>archived</Chip>}</h1>
          <p className="mt-1 break-all font-mono text-[11px] text-muted">{p.primary_path}</p>
          {p.description && <p className="mt-2 max-w-2xl text-sm text-muted">{p.description}</p>}
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <Btn onClick={() => setModal('task')}>+ Task</Btn><Btn kind="ghost" onClick={() => setModal('goal')}>+ Goal</Btn>
          <Link to={`/files?root=${encodeURIComponent('project:' + slug)}`} className="inline-flex items-center justify-center gap-2 rounded-full border border-line px-4 py-1.5 font-mono text-[11px] uppercase tracking-wider text-muted hover:text-fg">Files</Link>
          <ActionBtn url={`/api/project/${slug}/archive?archived=${p.archived ? 0 : 1}`} label={p.archived ? 'Restore' : 'Archive'} kind="ghost" confirm={p.archived ? undefined : `Archive ${p.name}? Its tasks leave the global Tasks view.`} />
          <div className="flex basis-full gap-2 sm:basis-auto">
          {[['tasks', `${p.tasks_done}/${p.tasks_total}`], ['runs', String(p.runs.length)], ['goals', `${p.goals.filter(g => g.status === 'released').length}/${p.goals.length}`]].map(([k, v]) => (
            <GlassCard key={k} className="min-w-16 flex-1 py-2 text-center sm:flex-none sm:min-w-20"><p className="font-mono text-base font-semibold sm:text-lg">{v}</p><Label>{k}</Label></GlassCard>
          ))}
          </div>
        </div>
      </div>
      <div className="mb-5 h-1 overflow-hidden rounded-full bg-inset"><div className="h-full bg-working" style={{ width: `${pct}%` }} /></div>
      <div className="mb-5"><ScopedChat kind="project" id={p.id} slug={slug} /></div>
      {stuck.length > 0 && (
        <div className="mb-5">
          <Label>Needs you · {stuck.length}</Label>
          <div className="mt-2 flex flex-col gap-2">{stuck.map(t => <TaskRow key={t.id} t={t} showProject={false} />)}</div>
        </div>
      )}
      <div className="mb-3 flex gap-1 rounded-full border border-line bg-glass p-0.5 w-fit">
        {TABS.map(t => <button key={t} onClick={() => setTab(t)} className={clsx('rounded-full px-3 py-1 font-mono text-[10px] uppercase', tab === t ? 'bg-fg text-bg' : 'text-muted hover:text-fg')}>{t}</button>)}
      </div>
      {tab === 'tasks' && (p.tasks.length ? <div className="flex flex-col gap-2">{[...p.tasks].sort((a, b) => (b.updated_at ?? b.created_at) - (a.updated_at ?? a.created_at)).map(t => <TaskRow key={t.id} t={t} showProject={false} />)}</div> : <Empty title="No tasks" />)}
      {tab === 'goals' && (p.goals.length ? <div className="grid gap-3 sm:grid-cols-2">{p.goals.map(g => (
        <GlassCard key={g.id}>
          <div className="flex items-start justify-between gap-2"><h3 className="text-sm font-semibold">#{g.id} {g.title}</h3><span className={clsx('font-mono text-[10px] uppercase', GOAL_TONE[g.status])}>{g.status}</span></div>
          {g.description && <p className="mt-1 text-xs text-muted">{g.description}</p>}
          <p className="mt-2 font-mono text-[10px] text-muted">{g.tasks_done}/{g.tasks_total} tasks done</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {g.status === 'draft' && <ActionBtn url={`/api/goal/${g.id}/plan`} label="Plan" kind="ghost" confirm={`Ask the Orchestrator to plan goal #${g.id}? A planning task is created in the backlog; no agent starts automatically.`} />}
            {g.status === 'planning' && <><ActionBtn url={`/api/goal/${g.id}/planned`} label="Mark planned" kind="ghost" /><ActionBtn url={`/api/goal/${g.id}/abandon`} label="Abandon" kind="warn" confirm={`Abandon planning for goal #${g.id} and return it to draft?`} /></>}
            {g.status === 'planned' && <ActionBtn url={`/api/goal/${g.id}/release`} label="Release" confirm={`Release goal #${g.id}? Its tasks become eligible to run as their dependencies complete.`} />}
          </div>
        </GlassCard>))}</div> : <Empty title="No goals" />)}
      {tab === 'notes' && ((notes.data?.notes ?? []).length
        ? <div className="flex flex-col gap-2">{(notes.data?.notes ?? []).map(n => <NoteRow key={n.id} n={n} areas={areas.data?.areas} />)}</div>
        : <Empty title="No notes linked" note="File a note to this project from the Second Brain and it shows up here." />)}
      {tab === 'runs' && (p.runs.length ? <div className="flex flex-col gap-2">{p.runs.map(r => (
        <Link key={r.id} to={`/tasks/${r.task_id}`} className="glass flex flex-wrap items-center gap-3 rounded-xl px-4 py-2 text-xs hover:bg-raised">
          <span className="font-mono text-muted">run #{r.id}</span><Agent name={r.agent_profile} /><span className="text-muted">task #{r.task_id}</span>
          <span className={clsx('font-mono uppercase', r.status === 'done' ? 'text-working' : r.status === 'running' ? 'text-queued' : 'text-needsyou')}>{r.status}</span>
          <span className="ml-auto font-mono text-[10px] text-muted">{ago(r.started_at)}</span>
        </Link>))}</div> : <Empty title="No runs yet" />)}
      {tab === 'activity' && (p.activity.length ? <GlassCard className="p-0">{p.activity.map(a => (
        <div key={a.id} className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-2 text-xs last:border-0">
          <span className="w-28 shrink-0 font-mono text-[10px] text-muted">{when(a.ts)}</span><Agent name={a.agent_profile} /><span className="font-mono text-muted">{a.action}</span>
          <span className="min-w-0 basis-full truncate sm:flex-1 sm:basis-auto">{a.task_id ? <Link to={`/tasks/${a.task_id}`} className="hover:text-accent-2">#{a.task_id} </Link> : null}{a.detail}</span>
        </div>))}</GlassCard> : <Empty title="No activity yet" />)}
    </section>
  )
}
