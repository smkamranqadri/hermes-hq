import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useProjects, useTasks } from '../api'
import { PageHeader } from '../components/GlassCard'
import { TaskRow } from '../components/TaskRow'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, Loading, Select, Input, Label } from '../components/ui'
import { ORDER, HUMAN_LABEL, type HumanState } from '../status'
import { usePageTitle } from '../usePageTitle'
import clsx from 'clsx'

export function Tasks() {
  usePageTitle('Tasks')
  const [sp, setSp] = useSearchParams()
  const project = sp.get('project') ?? ''
  const state = sp.get('state') ?? ''
  const q = sp.get('q') ?? ''
  const view = sp.get('view') ?? 'list'
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n, { replace: true }) }
  const [draft, setDraft] = useState(q)
  const projects = useProjects()
  const tasks = useTasks({ project, state, q })
  const d = tasks.data
  const groups = ORDER.map(s => [s, (d?.tasks ?? []).filter(t => t.human.state === s)] as const).filter(([, l]) => l.length)

  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="tasks" title="Tasks" right={
        <div className="flex gap-1 rounded-full border border-line bg-glass p-0.5">
          {(['list', 'board'] as const).map(v => (
            <button key={v} onClick={() => set('view', v)} className={clsx('rounded-full px-3 py-1 font-mono text-[10px] uppercase', view === v ? 'bg-fg text-bg' : 'text-muted hover:text-fg')}>{v}</button>
          ))}
        </div>} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select value={project} onChange={e => set('project', e.target.value)}>
          <option value="">All projects</option>
          {(projects.data?.projects ?? []).map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}
        </Select>
        <Select value={state} onChange={e => set('state', e.target.value)}>
          <option value="">All states</option>
          {(d?.stateOptions ?? ORDER).map(s => <option key={s} value={s}>{HUMAN_LABEL[s as HumanState] ?? s}</option>)}
        </Select>
        <form onSubmit={e => { e.preventDefault(); set('q', draft) }} className="flex gap-1">
          <Input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Search title, id, agent, project…" className="w-56" />
        </form>
        {d && <span className="ml-auto font-mono text-[10px] text-muted">{d.total} task{d.total === 1 ? '' : 's'}</span>}
      </div>
      {tasks.isLoading && <Loading />}
      {tasks.isError && <Empty error title="Could not load /api/tasks" note={String(tasks.error)} />}
      {d && d.total === 0 && <Empty title="No tasks match" note={project || state || q ? 'Try clearing a filter.' : 'Nothing in active projects yet.'} />}
      {d && view === 'list' && groups.map(([s, list]) => (
        <div key={s} className="mb-6">
          <div className="mb-2 flex items-center gap-2"><StatusBadge human={{ state: s }} compact /><Label>{list.length}</Label></div>
          <div className="flex flex-col gap-2">{list.map(t => <TaskRow key={t.id} t={t} showProject={!project} />)}</div>
        </div>
      ))}
      {d && view === 'board' && (
        <div className="flex gap-3 overflow-x-auto pb-2">
          {ORDER.map(s => (
            <div key={s} className="w-72 shrink-0">
              <div className="mb-2 flex items-center gap-2"><StatusBadge human={{ state: s }} compact /><Label>{d.stateCounts[s] ?? 0}</Label></div>
              <div className="flex flex-col gap-2">{(d.tasks.filter(t => t.human.state === s)).map(t => <TaskRow key={t.id} t={t} showProject={!project} stacked />)}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
