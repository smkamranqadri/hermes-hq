import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useOverview, ago } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { TaskRow } from '../components/TaskRow'
import { ActivityList } from '../components/ActivityList'
import { Empty, Loading, Label, Agent } from '../components/ui'
import { StatusBadge } from '../components/StatusBadge'
import { usePageTitle } from '../usePageTitle'

function Stat({ label, value, to, tone }: { label: string; value: number | string; to?: string; tone?: string }) {
  const inner = <GlassCard className="py-3 text-center transition hover:bg-raised"><p className={`font-mono text-2xl font-semibold ${tone ?? ''}`}>{value}</p><Label>{label}</Label></GlassCard>
  return to ? <Link to={to}>{inner}</Link> : inner
}

function NextSchedule() {
  const n = useQuery({ queryKey: ['schedules-next'], queryFn: () => fetch('/api/schedules/next').then(r => r.json()), refetchInterval: 60000 })
  if (!n.data?.next?.length) return null
  const first = n.data.next[0]
  const mins = Math.max(0, Math.round((first.at - Date.now() / 1000) / 60))
  const when = mins < 60 ? `${mins} min` : `${Math.round(mins / 60)} h`
  return <Link to="/schedules" className="font-mono text-[10px] text-muted hover:text-fg">⟳ next: {first.name} in {when}{n.data.total_enabled > 1 ? ` · ${n.data.total_enabled - 1} more` : ''}</Link>
}

export function Overview() {
  usePageTitle('Overview')
  const q = useOverview()
  const d = q.data
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="overview" title="Overview" right={<span className="flex flex-wrap items-center gap-3"><NextSchedule />{d && <span className="font-mono text-[10px] text-muted">{d.stats.paused ? 'dispatcher paused' : `cap ${d.stats.cap || '—'}`} · updated {ago(d.ts)}</span>}</span>} />
      {q.isLoading && <><div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><Loading rows={4} card /></div><div className="mt-6"><Loading rows={3} /></div></>}
      {q.isError && <Empty error title="Could not load /api/overview" note={String(q.error)} />}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Needs you" value={d.stats.needsyou} to="/tasks?state=needsyou" tone={d.stats.needsyou ? 'text-needsyou' : ''} />
            <Stat label={`Working · ${d.stats.slots_used}/${d.stats.cap} slots`} value={d.stats.working} to="/tasks?state=working" tone={d.stats.working ? 'text-working' : ''} />
            <Stat label="Queued" value={d.stats.queued} to="/tasks?state=queued" tone="text-queued" />
            <Stat label="Done · 24h" value={d.stats.done_today} to="/tasks?state=done" tone="text-done" />
          </div>
          <div className="mt-6">
            <div className="mb-2 flex items-center gap-2"><StatusBadge human={{ state: 'needsyou' }} compact /><Label>{d.needsyou.length}</Label></div>
            {d.needsyou.length ? <div className="flex flex-col gap-2">{d.needsyou.map(t => <TaskRow key={t.id} t={t} />)}</div>
              : <Empty title="Nothing needs you" note="No blocked, failed, stalled or held tasks in active projects." />}
          </div>
          <div className="mt-6 grid min-w-0 gap-6 lg:grid-cols-3">
            <div className="min-w-0 lg:col-span-2">
              <div className="mb-2 flex items-center gap-2"><StatusBadge human={{ state: 'working' }} compact /><Label>{d.working.length}{d.stats.open_reviews ? ` · ${d.stats.open_reviews} in review` : ''}</Label></div>
              {d.working.length ? <div className="flex flex-col gap-2">{d.working.map(t => (
                <Link key={t.id} to={`/tasks/${t.id}`} className="glass flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl px-4 py-3 text-sm hover:bg-raised">
                  <span className="font-mono text-xs text-muted">#{t.id}</span><span className="min-w-0 basis-full truncate font-medium sm:flex-1 sm:basis-auto">{t.title}</span>
                  <Agent name={t.last_run?.agent_profile ?? t.assignee_profile} />
                  <StatusBadge human={t.human} live={t.status === 'running'} />
                  <span className="font-mono text-[10px] text-muted">{t.last_run?.started_at ? `since ${ago(t.last_run.started_at)}` : ''}</span>
                </Link>))}</div> : <Empty title="No agent working right now" />}
              <div className="mt-6 mb-2 flex items-center gap-2"><StatusBadge human={{ state: 'queued' }} compact /><Label>{d.stats.queued}</Label></div>
              {d.queued.length ? <div className="flex flex-col gap-2">{d.queued.map(t => <TaskRow key={t.id} t={t} />)}</div> : <Empty title="Queue empty" />}
            </div>
            <div className="min-w-0">
              <div className="mb-2 flex items-center justify-between"><Label>Activity</Label><Link to="/activity" className="font-mono text-[10px] uppercase text-muted hover:text-fg">All →</Link></div>
              <ActivityList events={d.activity} compact />
            </div>
          </div>
        </>
      )}
    </section>
  )
}
