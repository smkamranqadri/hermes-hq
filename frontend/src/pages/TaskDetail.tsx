import { Link, useParams } from 'react-router-dom'
import clsx from 'clsx'
import { useTask, useSystem, ago, when } from '../api'
import { GlassCard } from '../components/GlassCard'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, Loading, Chip, Crumbs, Label, Agent } from '../components/ui'
import { Btn } from '../components/Modal'
import { usePageTitle } from '../usePageTitle'
import { useState } from 'react'
import { ActionBtn, FeedbackModal } from '../components/forms'
import { RunLog } from '../components/RunLog'
import { ScopedChat } from '../components/ScopedChat'


function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return <div><Label>{title}</Label><div className="mt-1 whitespace-pre-wrap break-words text-sm">{children}</div></div>
}

/** Phone-only collapsible section: tap-header under `sm`; plain label and always-open content on desktop. */
function Fold({ title, children, desk = true }: { title: string; children: React.ReactNode; desk?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" data-fold aria-expanded={open} onClick={() => setOpen(o => !o)} className="flex w-full items-center justify-between sm:hidden">
        <Label>{title}</Label>
        <span aria-hidden="true" className={clsx('font-mono text-sm text-muted transition-transform', open && 'rotate-90')}>{'\u203a'}</span>
      </button>
      {desk && <span className="hidden sm:block"><Label>{title}</Label></span>}
      <div className={clsx('min-w-0', !open && 'hidden', 'sm:block')}>{children}</div>
    </>
  )
}

export function TaskDetail() {
  const id = Number(useParams().id)
  const q = useTask(id)
  const sys = useSystem()
  const t = q.data
  usePageTitle(t ? `#${t.id} ${t.title}` : `Task #${id}`)
  const [reply, setReply] = useState(false)
  if (q.isLoading) return <section className="mx-auto max-w-6xl p-4 sm:p-6"><Loading rows={1} /><div className="mt-4 grid gap-4 lg:grid-cols-3"><div className="lg:col-span-2"><Loading rows={3} /></div><Loading rows={2} /></div></section>
  if (q.isError || !t) return <section className="mx-auto max-w-6xl p-6"><Empty error title={`Could not load /api/task/${id}`} note={String(q.error ?? '404')} /></section>
  const latest = t.runs[0]
  const st = t.status
  const canFeedback = ['needs_review', 'rework', 'done', 'blocked', 'failed', 'stalled'].includes(st)
  const canRetry = ['failed', 'stalled', 'blocked', 'rework', 'manual'].includes(st)
  const canManual = !['done', 'manual', 'running'].includes(st)
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <Crumbs items={[['Projects', '/projects'], [t.project_slug, `/projects/${t.project_slug}`], [`Task #${t.id}`]]} />
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="break-words text-lg font-semibold tracking-tight sm:text-xl">{t.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
            <StatusBadge human={{ state: t.human.state, reason: t.human.reason?.split(':')[0] }} live={st === 'running'} /><span className="font-mono">engine: {t.status}</span>{sys.data && (st === 'running' || st === 'ready') && <span className={`font-mono ${st === 'ready' && sys.data.running >= sys.data.cap ? 'text-needsyou' : ''}`}>· slots {sys.data.running}/{sys.data.cap} busy{st === 'ready' && !sys.data.paused && sys.data.running >= sys.data.cap ? ' — waiting for a free slot' : ''}{st === 'ready' && sys.data.paused ? ' — dispatcher paused' : ''}</span>}
            <Agent name={t.assignee_profile} />{!!t.is_code && <Chip>code</Chip>}<Chip>review {t.review_policy}</Chip>
            {t.goal_title && <span>· goal #{t.goal_id} {t.goal_title}</span>}
            {t.schedule_id != null && <Link to="/schedules" className="text-accent-2 hover:underline">· ⟳ created by a schedule</Link>}
          </div>
        </div>
        {t.human.reason && t.human.reason.includes(':') && <p className="basis-full text-sm text-needsyou">{t.human.reason.slice(t.human.reason.indexOf(':') + 1).trim()}</p>}
        <div className="flex basis-full flex-wrap gap-2">
          {st === 'planned' && <ActionBtn url={`/api/task/${t.id}/mark-ready`} label="Mark ready" confirm={t.goal_id && t.goal_status !== 'released' ? 'This bypasses the goal release gate for this task. Continue?' : undefined} />}
          {t.human.action === 'release_goal' && t.goal_id && <ActionBtn url={`/api/goal/${t.goal_id}/release`} label="Release goal" confirm={`Release goal #${t.goal_id}?`} />}
          {st === 'running' && <ActionBtn url={`/api/task/${t.id}/stop`} label="Stop" kind="warn" confirm="Kill the running agent? The run is marked failed and the task leaves the queue (manual)." />}
          {st === 'running' && <ActionBtn url={`/api/task/${t.id}/stop?keep_in_queue=1`} label="Stop & re-queue" kind="ghost" confirm="Kill the running agent and put the task back to ready for a fresh run?" />}
          {st === 'blocked' && <Btn onClick={() => setReply(true)}>Reply → rework</Btn>}
          {(st === 'failed' || st === 'stalled') && <ActionBtn url={`/api/task/${t.id}/retry`} label="Retry" confirm="Re-queue this task for a fresh run? Old runs are kept." />}
          {st === 'blocked' && <ActionBtn url={`/api/task/${t.id}/retry`} label="Retry as-is" kind="ghost" confirm="Retry without new information? The agent may block again." />}
          {st === 'rework' && <span className="inline-flex items-center gap-1.5 rounded-full border border-queued/50 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-queued"><span className="hq-dot-live size-1.5 rounded-full bg-current" />feedback sent · waiting for the agent</span>}
          {canFeedback && st !== 'blocked' && <Btn kind="ghost" onClick={() => setReply(true)}>{st === 'rework' ? 'Add feedback' : 'Feedback → rework'}</Btn>}
          {canRetry && !['failed', 'stalled', 'blocked'].includes(st) && <ActionBtn url={`/api/task/${t.id}/retry`} label="Re-queue" kind="ghost" confirm="Re-queue this task?" />}
          {canManual && <ActionBtn url={`/api/task/${t.id}/manual`} label="Take over" kind="warn" confirm="Take this task out of the queue (status manual)?" />}
        </div>
        {reply && <FeedbackModal taskId={t.id} onClose={() => setReply(false)} />}
      </div>
      <div className="grid min-w-0 gap-4 lg:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-4 lg:col-span-2">
          <GlassCard className="flex min-w-0 flex-col gap-4 overflow-hidden">
            {t.description && <Block title="Description">{t.description}</Block>}
            {t.definition_of_done && <Block title="Definition of done">{t.definition_of_done}</Block>}
            {t.summary && <Block title="Summary">{t.summary}</Block>}
            {t.feedback && <Block title="Owner feedback">{t.feedback}</Block>}
            {t.result_paths.length > 0 && <Block title="Results">{t.result_paths.map(p => <p key={p} className="break-all font-mono text-xs">{p}</p>)}</Block>}
          </GlassCard>
          {latest && <RunLog key={latest.id} runId={latest.id} active={latest.status === 'running'} />}
          <div>
            <Fold title={`Runs · ${t.runs.length}`}>
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
                    {r.session_id ? <span className="inline-flex flex-wrap items-center gap-2"><span className="break-all text-accent-2">session {r.session_id}</span><Link to={`/chat/${r.agent_profile}/${r.session_id}`} className={`rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider hover:bg-raised ${r.status === 'running' ? 'border-working/60 text-working' : 'border-line text-fg'}`}>{r.status === 'running' ? 'Watch session' : 'Open session'}</Link></span> : <span className="text-muted">session not mapped yet</span>}
                  </div>
                  {r.error && <p className="mt-1.5 whitespace-pre-wrap break-words text-error">{r.error}</p>}
                </GlassCard>))}</div>
            )}
            </Fold>
          </div>
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <ScopedChat kind="task" id={t.id} />
          {(t.deps.length > 0 || t.dependents.length > 0) && (
            <GlassCard className="text-xs">
              <Fold title={`Dependencies · ${t.deps.length + t.dependents.length}`} desk={false}>
              {t.deps.length > 0 && <><Label>Waits on</Label><ul className="mb-3 mt-1">{t.deps.map(d => <li key={d.id}><Link to={`/tasks/${d.id}`} className="hover:text-accent-2">#{d.id} {d.title}</Link> <span className="font-mono text-muted">{d.status}</span></li>)}</ul></>}
              {t.dependents.length > 0 && <><Label>Unblocks</Label><ul className="mt-1">{t.dependents.map(d => <li key={d.id}><Link to={`/tasks/${d.id}`} className="hover:text-accent-2">#{d.id} {d.title}</Link> <span className="font-mono text-muted">{d.status}</span></li>)}</ul></>}
              </Fold>
            </GlassCard>
          )}
          {t.reviews.length > 0 && (
            <GlassCard className="text-xs">
              <Fold title={`Reviews · ${t.reviews.length}`}>
              <div className="mt-2 flex flex-col gap-2">{t.reviews.map(r => (
                <div key={r.id} className="border-b border-line-subtle pb-2 last:border-0">
                  <div className="flex items-center gap-2"><Agent name={r.reviewer_profile} /><span className={clsx('font-mono uppercase', r.verdict === 'approved' ? 'text-working' : r.verdict ? 'text-needsyou' : 'text-muted')}>{r.verdict ?? r.status}</span><span className="ml-auto font-mono text-[10px] text-muted">{ago(r.decided_at ?? r.requested_at)}</span></div>
                  {r.comments && <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-muted">{r.comments}</p>}
                </div>))}</div>
              </Fold>
            </GlassCard>
          )}
          <GlassCard className="text-xs">
            <Fold title="History">
            <div className="mt-2 flex flex-col gap-1.5">{t.transitions.map(x => (
              <div key={x.id} className="flex gap-2"><span className="w-24 shrink-0 font-mono text-[10px] text-muted">{when(x.ts)}</span><span className="font-mono">{x.from_status ?? '∅'} → {x.to_status}</span>{x.detail && <span className="truncate text-muted">{x.detail}</span>}</div>))}</div>
            </Fold>
            <p className="mt-3 font-mono text-[10px] text-muted">created {when(t.created_at)}{latest ? ` · last run ${ago(latest.started_at)}` : ''}</p>
          </GlassCard>
        </div>
      </div>
    </section>
  )
}
