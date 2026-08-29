import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useActivity, useProjects, useRoster, type ActivityEvent } from '../api'
import { PageHeader } from '../components/GlassCard'
import { ActivityList } from '../components/ActivityList'
import { Empty, Loading, Select } from '../components/ui'
import { Btn } from '../components/Modal'
import { usePageTitle } from '../usePageTitle'

export function Activity() {
  usePageTitle('Activity')
  const [sp, setSp] = useSearchParams()
  const project = sp.get('project') ?? ''; const agent = sp.get('agent') ?? ''
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n, { replace: true }); setPages([]) }
  const projects = useProjects(); const roster = useRoster()
  const first = useActivity({ project, agent, limit: 100 })
  const [pages, setPages] = useState<ActivityEvent[][]>([])
  const [before, setBefore] = useState<number | undefined>()
  const more = useActivity({ project, agent, limit: 100, before })
  const events = [...(first.data?.events ?? []), ...pages.flat()]
  const nextBefore = pages.length ? more.data?.next_before : first.data?.next_before
  useEffect(() => { if (more.data && before) setPages(p => p.includes(more.data!.events) ? p : [...p, more.data!.events]) }, [more.data, before])
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="activity" title="Activity" />
      <div className="mb-4 flex flex-wrap gap-2">
        <Select value={project} onChange={e => set('project', e.target.value)}><option value="">All projects</option>{(projects.data?.projects ?? []).map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}</Select>
        <Select value={agent} onChange={e => set('agent', e.target.value)}><option value="">All agents</option>{(roster.data?.assignees ?? []).map(a => <option key={a}>{a}</option>)}</Select>
      </div>
      {first.isLoading && <Loading rows={8} />}
      {first.isError && <Empty error title="Could not load /api/activity" note={String(first.error)} />}
      {first.data && (events.length ? <ActivityList events={events} /> : <Empty title="No activity" />)}
      {nextBefore && <div className="mt-4 text-center"><Btn kind="ghost" busy={more.isFetching} onClick={() => setBefore(nextBefore)}>Load older</Btn></div>}
    </section>
  )
}
