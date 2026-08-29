import { Link, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { useTask, ago, when } from '../api'
import { GlassCard } from '../components/GlassCard'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, Loading, Chip, Crumbs, Label, Agent } from '../components/ui'
import { usePageTitle } from '../usePageTitle'

const ACTION_LABEL: Record<string, string> = { mark_ready: 'Mark ready', release_goal: 'Release goal', retry: 'Retry', unblock: 'Unblock' }

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return <div><Label>{title}</Label><div className="mt-1 whitespace-pre-wrap break-words text-sm">{children}</div></div>
}

export function TaskDetail() {
  const id = Number(useParams().id)
  const q = useTask(id)
  const t = q.data
  usePageTitle(t ? `#${t.id} ${t.title}` : `Task #${id}`)
  if (q.isLoading) return <section className="mx-auto max-w-6xl p-6"><Loading /></section>
  if (q.isError || !t) return <section className="mx-auto max-w-6xl p-6"><Empty error title={`Could not load /api/task/${id}`} note={String(q.error ?? '404')} /></section>
  const latest = t.runs[0]
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <Crumbs items={[['Projects', '/projects'], [t.project_slug, `/projects/${t.project_slug}`], [`Task #${t.id}`]]} />
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{t.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
            <StatusBadge human={{ state: t.human.state, reason: t.human.reason?.split(':')[0] }} /><span className="font-mono">engine: {t.status}</span>
            <Agent name={t.assignee_profile} />{!!t.is_code && <Chip>code</Chip>}<Chip>review {t.review_policy}</Chip>
            {t.goal_title && <span>· goal #{t.goal_id} {t.goal_title}</span>}
          </div>
        </div>
        {t.human.reason && t.human.reason.includes(':') && <p className="basis-full text-sm text-needsyou">{t.human.reason.slice(t.human.reason.indexOf(':') + 1).trim()}</p>}
        {t.human.action && (
          <button disabled title="Write actions arrive in Group 1b" className="rounded-full border border-needsyou/50 bg-needsyou/10 px-4 py-1.5 font-mono text-[11px] uppercase text-needsyou opacity-60">
            {ACTION_LABEL[t.human.action] ?? t.human.action} · soon
          </button>
        )}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="flex flex-col gap-4 lg:col-span-2">
          <GlassCard className="flex flex-col gap-4">
            {t.description && <Block title="Description">{t.description}</Block>}
            {t.definition_of_done && <Block title="Definition of done">{t.definition_of_done}</Block>}
            {t.summary && <Block title="Summary">{t.summary}</Block>}
            {t.feedback && <Block title="Owner feedback">{t.feedback}</Block>}
            {t.result_paths.length > 0 && <Block title="Results">{t.result_paths.map(p => <p key={p} className="break-all font-mono text-xs">{p}</p>)}</Block>}
          </GlassCard>
          <div>
            <Label>Runs · {t.runs.length}</Label>
            {t.runs.length === 0 ? <p className="mt-1 text-xs text-muted">No run yet — this task has never been dispatched.</p> : (
              <div className="mt-2 flex flex-col gap-2">{t.runs.map(r => (
                <GlassCard key={r.id} className="py-3 text-xs" accent={r.status === 'done' ? 'var(--hq-working)' : r.status === 'running' ? 'var(--hq-queued)' : 'var(--hq-needsyou)'}>
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="font-mono text-muted">run #{r.id}</span><Agent name={r.agent_profile} />
                    <span className={clsx('font-mono uppercase', r.status === 'done' ? 'text-working' : r.status === 'running' ? 'text-queued' : 'text-needsyou')}>{r.status}</span>
                    {r.branch && <Chip>{r.branch}</Chip>}
                    <span className="ml-auto font-mono text-[10px] text-muted">{when(r.started_at)} → {r.finished_at ? ago(r.finished_at) : 'running'}</span>
                  </div>
                  <div className="mt-1.5 font-mono text-[11px]">
                    {r.session_id ? <span className="text-accent-2">session {r.session_id}</span> : <span className="text-muted">session not mapped yet</span>}
                  </div>
                  {r.error && <p className="mt-1.5 whitespace-pre-wrap text-error">{r.error}</p>}
                </GlassCard>))}</div>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-4">
          {(t.deps.length > 0 || t.dependents.length > 0) && (
            <GlassCard className="text-xs">
              {t.deps.length > 0 && <><Label>Waits on</Label><ul className="mb-3 mt-1">{t.deps.map(d => <li key={d.id}><Link to={`/tasks/${d.id}`} className="hover:text-accent-2">#{d.id} {d.title}</Link> <span className="font-mono text-muted">{d.status}</span></li>)}</ul></>}
              {t.dependents.length > 0 && <><Label>Unblocks</Label><ul className="mt-1">{t.dependents.map(d => <li key={d.id}><Link to={`/tasks/${d.id}`} className="hover:text-accent-2">#{d.id} {d.title}</Link> <span className="font-mono text-muted">{d.status}</span></li>)}</ul></>}
            </GlassCard>
          )}
          {t.reviews.length > 0 && (
            <GlassCard className="text-xs">
              <Label>Reviews · {t.reviews.length}</Label>
              <div className="mt-2 flex flex-col gap-2">{t.reviews.map(r => (
                <div key={r.id} className="border-b border-line-subtle pb-2 last:border-0">
                  <div className="flex items-center gap-2"><Agent name={r.reviewer_profile} /><span className={clsx('font-mono uppercase', r.verdict === 'approved' ? 'text-working' : r.verdict ? 'text-needsyou' : 'text-muted')}>{r.verdict ?? r.status}</span><span className="ml-auto font-mono text-[10px] text-muted">{ago(r.decided_at ?? r.requested_at)}</span></div>
                  {r.comments && <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-muted">{r.comments}</p>}
                </div>))}</div>
            </GlassCard>
          )}
          <GlassCard className="text-xs">
            <Label>History</Label>
            <div className="mt-2 flex flex-col gap-1.5">{t.transitions.map(x => (
              <div key={x.id} className="flex gap-2"><span className="w-24 shrink-0 font-mono text-[10px] text-muted">{when(x.ts)}</span><span className="font-mono">{x.from_status ?? '∅'} → {x.to_status}</span>{x.detail && <span className="truncate text-muted">{x.detail}</span>}</div>))}</div>
            <p className="mt-3 font-mono text-[10px] text-muted">created {when(t.created_at)}{latest ? ` · last run ${ago(latest.started_at)}` : ''}</p>
          </GlassCard>
        </div>
      </div>
    </section>
  )
}
