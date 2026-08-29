import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useProjects, ago } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Loading, Chip, Select, Agent } from '../components/ui'
import { usePageTitle } from '../usePageTitle'
import { NewProjectModal } from '../components/forms'
import { Btn } from '../components/Modal'

export function Projects() {
  usePageTitle('Projects')
  const [scope, setScope] = useState<'active' | 'all'>('active')
  const [creating, setCreating] = useState(false)
  const q = useProjects(scope === 'all')
  const list = (q.data?.projects ?? []).filter(p => scope === 'all' || !p.archived)
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="projects" title="Projects" right={<div className="flex items-center gap-2">
        <Select value={scope} onChange={e => setScope(e.target.value as 'active' | 'all')}>
          <option value="active">Active</option><option value="all">All incl. archived</option>
        </Select>
        <Btn onClick={() => setCreating(true)}>+ Project</Btn></div>} />
      {creating && <NewProjectModal onClose={() => setCreating(false)} />}
      {q.isLoading && <Loading />}
      {q.isError && <Empty error title="Could not load /api/projects" note={String(q.error)} />}
      {q.data && list.length === 0 && <Empty title="No projects" note="Use + Project to create one." />}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {list.map(p => {
          const pct = p.tasks_total ? Math.round(100 * p.tasks_done / p.tasks_total) : 0
          return (
            <Link key={p.slug} to={`/projects/${p.slug}`} className="block">
              <GlassCard accent={p.archived ? 'var(--hq-muted)' : 'var(--hq-accent)'} className="h-full transition hover:bg-raised">
                <div className="flex items-start justify-between gap-2">
                  <h2 className="text-sm font-semibold">{p.name}</h2>
                  {p.archived ? <Chip>archived</Chip> : p.active_agents.length > 0 ? <Chip tone="accent">{p.active_agents.length} working</Chip> : null}
                </div>
                <p className="mt-0.5 font-mono text-[10px] text-muted">{p.slug}</p>
                {p.description && <p className="mt-2 line-clamp-2 text-xs text-muted">{p.description}</p>}
                <div className="mt-3 h-1 overflow-hidden rounded-full bg-inset"><div className="h-full bg-working" style={{ width: `${pct}%` }} /></div>
                <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-muted">
                  <span>{p.tasks_done}/{p.tasks_total} tasks</span>
                  <span>{p.goals_released}/{p.goals_total} goals released</span>
                  <span>{p.runs_total} runs</span>
                </div>
                {p.last_activity && (
                  <p className="mt-2 truncate text-[11px] text-muted"><Agent name={p.last_activity.agent_profile} /> {p.last_activity.action} · {ago(p.last_activity.ts)}</p>
                )}
              </GlassCard>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
