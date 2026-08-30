import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { get, post, useRoster, useProjects, ago } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Skeleton, Chip, Spinner } from '../components/ui'
import { Modal, Field, TextInput, TextArea, SelectInput, Btn } from '../components/Modal'
import { AgentSwitcher } from '../components/AgentSwitcher'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'

// Group 7 — Schedules: hq task schedules (dispatcher-fired WM tasks) + Hermes cron agent jobs.
type Sched = { id: number; name: string; cron: string; zone: string; project_slug: string; project_name: string; title: string; description: string; definition_of_done: string; assignee_profile: string | null; review_policy: string; is_code: number; overlap: 'skip' | 'always'; enabled: number; next_fire_at: number | null; last_fired_at: number | null; last_task_id: number | null; last_task_status: string | null; last_run_kind: string | null; last_run_ts: number | null; next_fires: number[]; cron_text: string; open_task: boolean }
type SchedRun = { ts: number; kind: string; task_id: number | null; detail: string | null; task_title: string | null; task_status: string | null }
type CronJob = { id: string; name: string; prompt: string; schedule: { expr?: string; display?: string } | string; schedule_display?: string; state: string; enabled: boolean; next_run_at: string | null; last_run_at: string | null; last_status: string | null; last_error: string | null; failure_streak: number; deliver: string; profile: string; legacy_wm: boolean; is_script: boolean; model: string | null; workdir: string | null }
type CronRun = { status: string | null; started_at: number | string | null; finished_at: number | string | null; error: string | null; output?: string | null; delivered?: boolean | null }

const q = (o: Record<string, string | number | undefined>) => Object.entries(o).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
const at = (t: number | null) => t == null ? '—' : new Date(t * 1000).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const inKhi = (t: number) => new Date(t * 1000).toLocaleString('en-GB', { timeZone: 'Asia/Karachi', weekday: 'short', hour: '2-digit', minute: '2-digit' })
const pill = (cls: string) => clsx('rounded-full border px-1.5 py-0.5 font-mono text-[10px]', cls)
const KIND: Record<string, string> = { fired: 'border-working/50 text-working', late: 'border-needsyou/50 text-needsyou', manual: 'border-accent/50 text-accent-2', skipped: 'border-line text-muted', error: 'border-error/50 text-error' }

export function Schedules() {
  usePageTitle('Schedules')
  const [params, setParamsRaw] = useSearchParams()
  const tab = params.get('tab') === 'agents' ? 'agents' : 'tasks'
  const setParams = (patch: Record<string, string>) => setParamsRaw(p => { const n = new URLSearchParams(p); for (const [k, v] of Object.entries(patch)) { if (v) n.set(k, v); else n.delete(k) } return n }, { replace: true })
  return (
    <section className="mx-auto max-w-7xl p-4 sm:p-6">
      <PageHeader crumb="schedules" title="Schedules" right={
        <div role="tablist" className="flex rounded-full border border-line bg-glass p-0.5 font-mono text-[10px] uppercase tracking-wider">
          {(['tasks', 'agents'] as const).map(t => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setParams({ tab: t })} className={clsx('rounded-full px-2.5 py-1', tab === t ? 'bg-accent/20 text-fg' : 'text-muted hover:text-fg')}>{t === 'tasks' ? 'Task schedules' : 'Agent jobs'}</button>)}
        </div>} />
      {tab === 'tasks' ? <TaskSchedules /> : <AgentJobs profile={params.get('profile') || 'all'} setProfile={p => setParams({ profile: p })} />}
    </section>
  )
}

// ---------------------------------------------------------------- Task schedules
function TaskSchedules() {
  const qc = useQueryClient(); const toast = useToast()
  const list = useQuery({ queryKey: ['schedules'], queryFn: () => get<{ schedules: Sched[]; zone: string }>('/api/schedules'), refetchInterval: 30000 })
  const [editing, setEditing] = useState<Sched | 'new' | null>(null)
  const [runsFor, setRunsFor] = useState<Sched | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['schedules'] })
  const act = async (key: string, fn: () => Promise<unknown>, msg?: string) => { setBusy(key); try { await fn(); if (msg) toast(msg); refresh() } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(null) } }
  const rows = list.data?.schedules ?? []
  return (
    <div className="min-w-0">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[10px] uppercase tracking-widest text-muted">{rows.length} schedule{rows.length === 1 ? '' : 's'} · times in Asia/Karachi · fired by the dispatcher even while dispatching is paused</p>
        <Btn onClick={() => setEditing('new')}>New schedule</Btn>
      </div>
      {list.isLoading ? <div className="grid gap-3 lg:grid-cols-2"><Skeleton rows={4} card /><Skeleton rows={4} card /></div>
        : rows.length === 0 ? <GlassCard><Empty title="No task schedules" note="A schedule creates a real task on its cadence — it goes through claim → run → review like any other." /></GlassCard>
        : <div className="grid gap-3 lg:grid-cols-2">
          {rows.map(s => (
            <GlassCard key={s.id} className={clsx('flex min-w-0 flex-col gap-2', !s.enabled && 'opacity-70')}>
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                <span className="truncate text-sm font-semibold">{s.name}</span>
                {s.last_run_kind && <span className={pill(KIND[s.last_run_kind] ?? 'border-line text-muted')} title={s.last_run_ts ? at(s.last_run_ts) : undefined}>{s.last_run_kind}</span>}
                {!s.enabled && <span className={pill('border-needsyou/50 text-needsyou')}>paused</span>}
                {s.open_task && <span className={pill('border-needsyou/50 text-needsyou')} title={`task #${s.last_task_id} is ${s.last_task_status}`}>previous open</span>}
                <button role="switch" aria-checked={!!s.enabled} aria-label={`${s.enabled ? 'Pause' : 'Resume'} ${s.name}`} disabled={busy === `t:${s.id}`} onClick={() => act(`t:${s.id}`, () => post(`/api/schedules/${s.id}/${s.enabled ? 'pause' : 'resume'}`), s.enabled ? 'Paused' : 'Resumed')} className={clsx('relative ml-auto h-5 w-9 shrink-0 rounded-full border transition', s.enabled ? 'border-accent bg-accent/60' : 'border-line bg-inset')}><span className={clsx('absolute top-0.5 size-3.5 rounded-full bg-fg transition', s.enabled ? 'left-4' : 'left-0.5')} /></button>
              </div>
              <p className="font-mono text-[11px] text-muted">{s.cron_text} <span className="opacity-60">({s.cron})</span>{s.enabled ? <> · next {s.next_fires[0] ? `${inKhi(s.next_fires[0])} PKT` : '—'}</> : null}</p>
              <p className="truncate text-xs"><span className="text-muted">creates</span> {s.title} <span className="text-muted">in</span> {s.project_slug}{s.assignee_profile && <> <span className="text-muted">for</span> {s.assignee_profile}</>}</p>
              <div className="flex flex-wrap items-center gap-1 font-mono text-[10px] text-muted">
                <span className={pill('border-line text-muted')}>{s.overlap === 'skip' ? 'skip when open' : 'always create'}</span>
                {!!s.is_code && <Chip>code</Chip>}<span className={pill('border-line text-muted')}>review {s.review_policy}</span>
                {s.last_task_id && <Link to={`/tasks/${s.last_task_id}`} className="text-accent-2 hover:underline">last task #{s.last_task_id} ({s.last_task_status})</Link>}
              </div>
              <div className="mt-auto flex flex-wrap justify-end gap-2">
                <Btn kind="ghost" onClick={() => setRunsFor(s)}>Log</Btn>
                <Btn kind="ghost" busy={busy === `r:${s.id}`} onClick={() => act(`r:${s.id}`, async () => { const r = await post<{ task_id: number }>(`/api/schedules/${s.id}/run`); toast(`Task #${r.task_id} created`) })}>Run now</Btn>
                <Btn kind="ghost" onClick={() => setEditing(s)}>Edit</Btn>
              </div>
            </GlassCard>))}
        </div>}
      {editing && <SchedModal s={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); refresh() }} />}
      {runsFor && <RunsModal s={runsFor} onClose={() => setRunsFor(null)} onChanged={refresh} />}
    </div>
  )
}

function useCronPreview(cron: string) {
  return useQuery({ queryKey: ['cron-preview', cron], queryFn: () => get<{ text: string; next_fires: number[] }>(`/api/schedules/preview?${q({ cron })}`), enabled: cron.trim().length > 0, retry: false, staleTime: 60000 })
}

function SchedModal({ s, onClose, onDone }: { s: Sched | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast(); const roster = useRoster(); const projects = useProjects()
  const [f, setF] = useState(() => s ? { name: s.name, cron: s.cron, project: s.project_slug, title: s.title, description: s.description, definition_of_done: s.definition_of_done, assignee: s.assignee_profile ?? '', review_policy: s.review_policy, is_code: !!s.is_code, overlap: s.overlap } : { name: '', cron: '0 9 * * *', project: '', title: '', description: '', definition_of_done: '', assignee: '', review_policy: 'none', is_code: false, overlap: 'skip' as const })
  const [preset, setPreset] = useState({ kind: 'custom', at: '09:00', dow: 'mon', day: 1, every_hours: 6 })
  const [busy, setBusy] = useState(false); const [confirmDel, setConfirmDel] = useState(false)
  const pv = useCronPreview(f.cron)
  useEffect(() => {
    if (preset.kind === 'custom') return
    post<{ cron: string }>('/api/schedules/compile', preset).then(r => setF(x => ({ ...x, cron: r.cron }))).catch(() => undefined)
  }, [preset])
  const submit = async () => {
    setBusy(true)
    try {
      if (s) await post(`/api/schedules/${s.id}`, { ...f, assignee: f.assignee || undefined, clear_assignee: !f.assignee })
      else await post('/api/schedules', { ...f, assignee: f.assignee || undefined })
      toast(s ? 'Saved' : 'Schedule created'); onDone()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF(x => ({ ...x, [k]: e.target.value }))
  return (
    <Modal title={s ? `Edit “${s.name}”` : 'New task schedule'} onClose={onClose}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><TextInput value={f.name} onChange={set('name')} placeholder="Weekly brief" /></Field>
        <Field label="Project"><SelectInput value={f.project} onChange={set('project')}><option value="">pick…</option>{projects.data?.projects.map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}</SelectInput></Field>
        <Field label="Cadence">
          <SelectInput value={preset.kind} onChange={e => setPreset(x => ({ ...x, kind: e.target.value }))}>
            <option value="custom">custom cron…</option><option value="daily">every day</option><option value="weekdays">weekdays</option><option value="weekly">weekly</option><option value="monthly">monthly</option><option value="hours">every N hours</option>
          </SelectInput>
        </Field>
        <Field label={preset.kind === 'hours' ? 'Every / minute' : 'At (PKT)'}>
          <div className="flex gap-2">
            {preset.kind === 'weekly' && <SelectInput value={preset.dow} onChange={e => setPreset(x => ({ ...x, dow: e.target.value }))}>{['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(d => <option key={d}>{d}</option>)}</SelectInput>}
            {preset.kind === 'monthly' && <TextInput type="number" min={1} max={28} value={preset.day} onChange={e => setPreset(x => ({ ...x, day: Number(e.target.value) }))} className="w-20" aria-label="Day of month" />}
            {preset.kind === 'hours' && <TextInput type="number" min={1} max={24} value={preset.every_hours} onChange={e => setPreset(x => ({ ...x, every_hours: Number(e.target.value) }))} className="w-20" aria-label="Hours" />}
            {preset.kind !== 'custom' && <TextInput type="time" value={preset.at} onChange={e => setPreset(x => ({ ...x, at: e.target.value }))} aria-label="Time" />}
            {preset.kind === 'custom' && <TextInput value={f.cron} onChange={e => { setF(x => ({ ...x, cron: e.target.value })) }} placeholder="0 9 * * 1" aria-label="Cron expression" />}
          </div>
        </Field>
        <p className="text-[11px] text-muted sm:col-span-2">{pv.isError ? <span className="text-error">{(pv.error as Error).message}</span> : pv.data ? <>⟳ {pv.data.text} · next: {pv.data.next_fires.map(t => inKhi(t)).join(' · ')} <span className="opacity-60">PKT</span></> : '…'}</p>
        <div className="sm:col-span-2"><Field label="Task title" hint="{date} {week} {month} expand at fire time"><TextInput value={f.title} onChange={set('title')} placeholder="Publish weekly brief {week}" /></Field></div>
        <div className="sm:col-span-2"><Field label="Description"><TextArea value={f.description} onChange={set('description')} /></Field></div>
        <div className="sm:col-span-2"><Field label="Definition of done"><TextArea value={f.definition_of_done} onChange={set('definition_of_done')} /></Field></div>
        <Field label="Assignee"><SelectInput value={f.assignee} onChange={set('assignee')}><option value="">any (dispatcher decides)</option>{roster.data?.assignees.map(a => <option key={a}>{a}</option>)}</SelectInput></Field>
        <Field label="Review"><SelectInput value={f.review_policy} onChange={set('review_policy')}>{(roster.data?.review_policies ?? ['none', 'required', 'optional']).map(r => <option key={r}>{r}</option>)}</SelectInput></Field>
        <Field label="If the previous task is still open"><SelectInput value={f.overlap} onChange={set('overlap')}><option value="skip">skip that firing</option><option value="always">create anyway</option></SelectInput></Field>
        <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={f.is_code} onChange={e => setF(x => ({ ...x, is_code: e.target.checked }))} /> code task (worktree)</label>
      </div>
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        {s && (confirmDel
          ? <><Btn kind="ghost" onClick={() => setConfirmDel(false)}>Cancel</Btn><Btn kind="warn" onClick={async () => { await post(`/api/schedules/${s.id}/delete`); toast('Schedule deleted (its tasks stay)'); onDone() }}>Confirm delete</Btn></>
          : <Btn kind="warn" onClick={() => setConfirmDel(true)}>Delete</Btn>)}
        <span className="flex-1" />
        <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
        <Btn busy={busy} disabled={!f.name.trim() || !f.project || !f.title.trim() || pv.isError} onClick={submit}>{s ? 'Save' : 'Create'}</Btn>
      </div>
    </Modal>
  )
}

function RunsModal({ s, onClose }: { s: Sched; onClose: () => void; onChanged?: () => void }) {
  const runs = useQuery({ queryKey: ['schedule-runs', s.id], queryFn: () => get<{ runs: SchedRun[] }>(`/api/schedules/${s.id}/runs`) })
  return (
    <Modal title={`Log — ${s.name}`} onClose={onClose}>
      {runs.isLoading ? <Skeleton rows={5} /> : !runs.data?.runs.length ? <Empty title="Nothing yet" note="Firings, skips and errors land here." /> : (
        <ul className="grid max-h-[60dvh] gap-1 overflow-auto">
          {runs.data.runs.map((r, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-2 text-xs">
              <span className={pill(KIND[r.kind] ?? 'border-line text-muted')}>{r.kind}</span>
              <span className="font-mono text-[10px] text-muted">{at(r.ts)}</span>
              {r.task_id && <Link to={`/tasks/${r.task_id}`} className="text-accent-2 hover:underline">#{r.task_id} {r.task_title}</Link>}
              {r.task_status && <Chip tone="muted">{r.task_status}</Chip>}
              {r.detail && <span className="basis-full text-muted">{r.detail}</span>}
            </li>))}
        </ul>)}
    </Modal>
  )
}

// ---------------------------------------------------------------- Agent jobs (Hermes cron)
function AgentJobs({ profile, setProfile }: { profile: string; setProfile: (p: string) => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const list = useQuery({ queryKey: ['cron-jobs', profile], queryFn: () => get<{ jobs: CronJob[] }>(`/api/cron/jobs?${q({ profile })}`), refetchInterval: 30000 })
  const [editing, setEditing] = useState<CronJob | 'new' | null>(null)
  const [runsFor, setRunsFor] = useState<CronJob | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
  const refresh = () => { void get(`/api/cron/jobs?${q({ profile, fresh: 1 })}`).then(() => qc.invalidateQueries({ queryKey: ['cron-jobs'] })) }
  const act = async (key: string, fn: () => Promise<unknown>, msg?: string) => { setBusy(key); try { await fn(); if (msg) toast(msg); refresh() } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(null) } }
  const jobs = list.data?.jobs ?? []
  const schedText = (j: CronJob) => j.schedule_display || (typeof j.schedule === 'string' ? j.schedule : j.schedule?.display || j.schedule?.expr || '')
  return (
    <div className="min-w-0">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select value={profile} onChange={e => setProfile(e.target.value)} aria-label="Agent" className="hq-select appearance-none rounded-lg border border-line bg-inset py-1.5 pl-2 pr-8 text-sm outline-none focus:border-accent"><option value="all">All agents</option><option value="orchestrator">Orchestrator</option>{['analyst', 'coder', 'marketer', 'reviewer', 'uiux', 'writer'].map(p => <option key={p}>{p}</option>)}</select>
        <p className="font-mono text-[10px] uppercase tracking-widest text-muted">{jobs.length} job{jobs.length === 1 ? '' : 's'} · fire from the agent's gateway (hq ticks agents whose gateway is off)</p>
        <span className="flex-1" />
        <Btn onClick={() => setEditing('new')}>New job</Btn>
      </div>
      {list.isLoading ? <div className="grid gap-3 lg:grid-cols-2"><Skeleton rows={4} card /><Skeleton rows={4} card /></div>
        : list.isError ? <GlassCard><Empty title="Could not list agent jobs" note={(list.error as Error).message} error /></GlassCard>
        : jobs.length === 0 ? <GlassCard><Empty title="No agent jobs" note="An agent job runs a prompt on a schedule inside the agent (Hermes cron) — its result lands in the agent's sessions and delivery target." /></GlassCard>
        : <div className="grid gap-3 lg:grid-cols-2">
          {jobs.map(j => (
            <GlassCard key={j.id + j.profile} className={clsx('flex min-w-0 flex-col gap-2', (j.state === 'paused' || !j.enabled) && 'opacity-70')}>
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                <span className="truncate text-sm font-semibold">{j.name || j.id}</span>
                <span className={pill(j.state === 'paused' ? 'border-needsyou/50 text-needsyou' : 'border-working/50 text-working')}>{j.state}</span>
                <span className={pill('border-line text-muted')}>{j.profile}</span>
                {j.is_script && <span className={pill('border-line text-muted')}>script</span>}
                {j.legacy_wm && <span className={pill('border-needsyou/50 text-needsyou')} title="Legacy Work Manager rollback path — keep paused">legacy WM</span>}
                {j.failure_streak > 0 && <span className={pill('border-error/50 text-error')}>{j.failure_streak}× failing</span>}
              </div>
              <p className="font-mono text-[11px] text-muted">{schedText(j)} · next {j.next_run_at ? new Date(j.next_run_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'} · deliver {j.deliver}</p>
              {j.prompt && <p className="line-clamp-2 text-xs text-muted" title={j.prompt}>{j.prompt}</p>}
              {j.last_status === 'error' && <p className="rounded-lg border border-error/40 bg-error/10 p-2 text-xs text-error">{j.last_error || 'last run failed'}</p>}
              <div className="mt-auto flex flex-wrap justify-end gap-2">
                <Btn kind="ghost" onClick={() => setRunsFor(j)}>History</Btn>
                {!j.legacy_wm && <Btn kind="ghost" busy={busy === `t:${j.id}`} onClick={() => act(`t:${j.id}`, () => post(`/api/cron/jobs/${j.id}/trigger`, { profile: j.profile }), 'Queued for the next ticker pass')}>Run now</Btn>}
                <Btn kind="ghost" busy={busy === `p:${j.id}`} onClick={() => act(`p:${j.id}`, () => post(`/api/cron/jobs/${j.id}/${j.state === 'paused' ? 'resume' : 'pause'}`, { profile: j.profile }), j.state === 'paused' ? 'Resumed' : 'Paused')}>{j.state === 'paused' ? 'Resume' : 'Pause'}</Btn>
                {!j.is_script && !j.legacy_wm && <Btn kind="ghost" onClick={() => setEditing(j)}>Edit</Btn>}
                {!j.legacy_wm && (confirm === j.id
                  ? <><Btn kind="ghost" onClick={() => setConfirm(null)}>Cancel</Btn><Btn kind="warn" busy={busy === `d:${j.id}`} onClick={() => act(`d:${j.id}`, () => post(`/api/cron/jobs/${j.id}/delete`, { profile: j.profile }), 'Deleted').then(() => setConfirm(null))}>Confirm</Btn></>
                  : <Btn kind="warn" onClick={() => setConfirm(j.id)}>Delete</Btn>)}
              </div>
            </GlassCard>))}
        </div>}
      {editing && <CronModal j={editing === 'new' ? null : editing} defaultProfile={profile === 'all' ? 'orchestrator' : profile} onClose={() => setEditing(null)} onDone={() => { setEditing(null); refresh() }} />}
      {runsFor && <CronRunsModal j={runsFor} onClose={() => setRunsFor(null)} />}
    </div>
  )
}

function CronModal({ j, defaultProfile, onClose, onDone }: { j: CronJob | null; defaultProfile: string; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const targets = useQuery({ queryKey: ['cron-targets'], queryFn: () => get<{ targets: { id: string; name: string; home_target_set: boolean }[] }>('/api/cron/targets'), staleTime: 300000 })
  const [f, setF] = useState(() => j ? { profile: j.profile, name: j.name, prompt: j.prompt, mode: 'cron', cron: (typeof j.schedule === 'object' && j.schedule?.expr) || '', n: 30, unit: 'minutes', deliver: j.deliver || 'local' } : { profile: defaultProfile, name: '', prompt: '', mode: 'every', cron: '0 9 * * *', n: 30, unit: 'minutes', deliver: 'local' })
  const [busy, setBusy] = useState(false)
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF(x => ({ ...x, [k]: e.target.value }))
  const submit = async () => {
    setBusy(true)
    try {
      const sched = f.mode === 'cron' ? { schedule: f.cron } : { every: { n: Number(f.n), unit: f.unit } }
      if (j) await post(`/api/cron/jobs/${j.id}/update`, { profile: j.profile, updates: { name: f.name, prompt: f.prompt, deliver: f.deliver, ...(f.mode === 'cron' ? { schedule: f.cron } : { schedule: `*/${f.n} * * * *` }) } })
      else await post('/api/cron/jobs', { profile: f.profile, name: f.name, prompt: f.prompt, deliver: f.deliver, ...sched })
      toast(j ? 'Saved' : 'Job created — it fires from the agent\'s gateway'); onDone()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title={j ? `Edit “${j.name || j.id}”` : 'New agent job'} onClose={onClose}>
      <div className="grid gap-3 sm:grid-cols-2">
        {!j && <Field label="Agent"><SelectInput value={f.profile} onChange={set('profile')}><option value="orchestrator">Orchestrator</option>{['analyst', 'coder', 'marketer', 'reviewer', 'uiux', 'writer'].map(p => <option key={p}>{p}</option>)}</SelectInput></Field>}
        <Field label="Name"><TextInput value={f.name} onChange={set('name')} placeholder="Morning digest" /></Field>
        <Field label="Cadence"><div className="flex gap-2">
          <SelectInput value={f.mode} onChange={set('mode')} aria-label="Cadence kind"><option value="every">every…</option><option value="cron">cron…</option></SelectInput>
          {f.mode === 'every' ? <><TextInput type="number" min={1} value={f.n} onChange={set('n')} className="w-20" aria-label="N" /><SelectInput value={f.unit} onChange={set('unit')} aria-label="Unit"><option value="minutes">minutes</option><option value="hours">hours</option></SelectInput></>
            : <TextInput value={f.cron} onChange={set('cron')} placeholder="0 4 * * *" aria-label="Cron (UTC)" />}
        </div></Field>
        <Field label="Deliver to"><SelectInput value={f.deliver} onChange={set('deliver')}>{(targets.data?.targets ?? [{ id: 'local', name: 'Local (save only)', home_target_set: true }]).map(t => <option key={t.id} value={t.id} disabled={!t.home_target_set}>{t.name}{t.home_target_set ? '' : ' — configure first'}</option>)}</SelectInput></Field>
        <div className="sm:col-span-2"><Field label="Prompt" hint="what the agent does each run"><TextArea rows={4} value={f.prompt} onChange={set('prompt')} placeholder="Summarize overnight mentions of …" /></Field></div>
        {f.mode === 'cron' && <p className="text-[11px] text-muted sm:col-span-2">Hermes cron expressions run in the server clock (UTC) — 04:00 UTC = 09:00 PKT.</p>}
      </div>
      <div className="mt-4 flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn busy={busy} disabled={!f.name.trim() || !f.prompt.trim()} onClick={submit}>{j ? 'Save' : 'Create'}</Btn></div>
    </Modal>
  )
}

function CronRunsModal({ j, onClose }: { j: CronJob; onClose: () => void }) {
  const runs = useQuery({ queryKey: ['cron-runs', j.id], queryFn: () => get<{ runs: CronRun[] }>(`/api/cron/jobs/${j.id}/runs?${q({ profile: j.profile })}`) })
  const ts = (v: number | string | null) => v == null ? '—' : typeof v === 'number' ? at(v) : new Date(v).toLocaleString()
  return (
    <Modal title={`History — ${j.name || j.id}`} onClose={onClose}>
      {runs.isLoading ? <Skeleton rows={5} /> : !runs.data?.runs?.length ? <Empty title="No attempts recorded" /> : (
        <ul className="grid max-h-[60dvh] gap-1 overflow-auto">
          {runs.data.runs.map((r, i) => (
            <li key={i} className="rounded-lg border border-line p-2 text-xs">
              <span className={pill(r.error || r.status === 'error' ? 'border-error/50 text-error' : 'border-working/50 text-working')}>{r.error ? 'error' : r.status ?? (r.finished_at ? 'ok' : 'running')}</span>
              <span className="ml-2 font-mono text-[10px] text-muted">{ts(r.started_at)}{r.finished_at ? ` → ${ts(r.finished_at)}` : ''}</span>
              {r.error && <p className="mt-1 text-error">{r.error}</p>}
              {r.output && <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-inset p-2 font-mono text-[11px] text-muted">{String(r.output).slice(0, 2000)}</pre>}
            </li>))}
        </ul>)}
    </Modal>
  )
}
