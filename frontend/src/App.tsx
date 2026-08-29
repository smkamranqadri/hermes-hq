import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { TopBar, TABS, TOOLS } from './components/TopBar'
import { GlassCard, PageHeader } from './components/GlassCard'
import { ToastProvider } from './components/Toast'
import { LoginScreen } from './components/LoginScreen'
import { ActionBtn } from './components/forms'
import { Btn } from './components/Modal'
import { Loading } from './components/ui'
import { post, setCsrf, useSession } from './api'
import { usePageTitle } from './usePageTitle'
import { Overview } from './pages/Overview'
import { Activity } from './pages/Activity'
import { Projects } from './pages/Projects'
import { ProjectDetail } from './pages/ProjectDetail'
import { Tasks } from './pages/Tasks'
import { TaskDetail } from './pages/TaskDetail'

function Placeholder({ title }: { title: string }) {
  usePageTitle(title)
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb={title.toLowerCase()} title={title} />
      <GlassCard><p className="text-sm text-muted">Not built yet. Nothing here is simulated.</p></GlassCard>
    </section>
  )
}

const fmtB = (n?: number | null) => n == null ? '—' : n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} KB`
const fmtUp = (s?: number | null) => s == null ? '—' : s > 86400 ? `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`

function Tile({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: 'ok' | 'warn' | 'err' }) {
  const c = tone === 'ok' ? 'text-working' : tone === 'warn' ? 'text-needsyou' : tone === 'err' ? 'text-error' : ''
  return <GlassCard className="py-3"><p className="font-mono text-[10px] uppercase tracking-widest text-muted">{label}</p><p className={`mt-1 font-mono text-lg font-semibold ${c}`}>{value}</p>{sub && <p className="mt-0.5 text-[11px] text-muted">{sub}</p>}</GlassCard>
}

function System() {
  usePageTitle('System')
  const qc = useQueryClient()
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()), refetchInterval: 5000 })
  const st = useQuery({ queryKey: ['system-stats'], queryFn: () => fetch('/api/system/stats').then(r => r.json()), refetchInterval: 10000 })
  const d = sys.data; const x = st.data
  const memPct = x?.host?.mem?.total ? Math.round(100 * (1 - x.host.mem.available / x.host.mem.total)) : null
  const diskPct = x?.host?.disk_hq ? Math.round(100 * (1 - x.host.disk_hq.free / x.host.disk_hq.total)) : null
  const gwOk = x?.hermes?.gateway?.status === 'ok'
  const procs = x?.hermes?.agent_processes ? Object.entries(x.hermes.agent_processes as Record<string, number>) : []
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="system" title="System" right={<Btn kind="ghost" onClick={async () => { await post('/api/logout'); window.dispatchEvent(new Event('hq:unauthenticated')) }}>Sign out</Btn>} />
      <GlassCard accent="var(--hq-accent)" className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">Dispatcher</h2>
            <p className="mt-1 text-xs text-muted">
              {d?.dispatcher?.enabled ? (d?.dispatcher?.alive ? 'loop alive' : 'loop not running') : 'disabled for this server process (--no-dispatcher)'}
              {' · '}{d?.paused ? 'PAUSED — nothing will be claimed' : 'active'}
              {d?.dispatcher?.last_tick ? ` · last tick ${new Date(d.dispatcher.last_tick * 1000).toLocaleTimeString()}` : ''}
            </p>
            {d?.dispatcher?.last_error && <p className="mt-1 text-xs text-error">{d.dispatcher.last_error}</p>}
          </div>
          <div className="flex flex-wrap gap-2">
            {d?.paused
              ? <ActionBtn url="/api/system/resume" label="Resume" confirm="Resume dispatching? Ready tasks will be claimed and agents launched." onDone={() => qc.invalidateQueries()} />
              : <ActionBtn url="/api/system/pause" label="Pause" kind="warn" confirm="Pause the dispatcher? Running agents finish; nothing new starts." onDone={() => qc.invalidateQueries()} />}
            <ActionBtn url="/api/system/dispatch" label="Tick now" kind="ghost" confirm="Run one dispatcher tick now?" onDone={() => qc.invalidateQueries()} />
          </div>
        </div>
      </GlassCard>
      {st.isLoading && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Loading rows={4} card /></div>}
      {x && (
        <>
          <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-muted">Hermes</p>
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Gateway :8642" value={gwOk ? 'OK' : 'DOWN'} tone={gwOk ? 'ok' : 'err'} sub={gwOk ? `hermes-agent ${x.hermes.gateway.version}` : x.hermes.gateway?.error} />
            <Tile label="Dashboard :9119" value={x.hermes.dashboard?.gateway_state ?? x.hermes.dashboard?.error ?? '—'} tone={x.hermes.dashboard?.gateway_running ? 'ok' : 'warn'} sub={x.hermes.dashboard?.version ? `v${x.hermes.dashboard.version}` : undefined} />
            <Tile label="Agent processes" value={procs.reduce((a, [, n]) => a + n, 0)} sub={procs.length ? procs.map(([p, n]) => `${p}×${n}`).join(' · ') : 'none running'} />
            <Tile label="Profiles" value={x.hermes.profiles.length} sub={x.hermes.profiles.join(', ')} />
          </div>
          <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-muted">Store</p>
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Tasks" value={x.store?.counts.tasks ?? '—'} sub={`${x.store?.counts.projects} projects · ${x.store?.counts.goals} goals`} />
            <Tile label="Runs" value={x.store?.counts.runs ?? '—'} sub={`${x.store?.runs_running ?? 0} running · ${x.store?.counts.reviews} reviews`} tone={x.store?.runs_running ? 'ok' : undefined} />
            <Tile label="Activity" value={x.store?.counts.activity ?? '—'} sub={x.store?.last_activity ? `last ${new Date(x.store.last_activity * 1000).toLocaleString()}` : 'none'} />
            <Tile label="On disk" value={fmtB((x.store?.db_bytes ?? 0) + x.runs_dir.bytes)} sub={`db ${fmtB(x.store?.db_bytes)} · runs ${fmtB(x.runs_dir.bytes)}`} />
          </div>
          <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-muted">Host</p>
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Load" value={x.host.load ? x.host.load.map((n: number) => n.toFixed(2)).join(' ') : '—'} sub={`${x.host.cpus} cpus`} tone={x.host.load && x.host.load[0] > (x.host.cpus ?? 1) ? 'warn' : undefined} />
            <Tile label="Memory" value={memPct != null ? `${memPct}%` : '—'} sub={`${fmtB(x.host.mem?.available)} free of ${fmtB(x.host.mem?.total)}`} tone={memPct != null && memPct > 90 ? 'warn' : undefined} />
            <Tile label="Disk" value={diskPct != null ? `${diskPct}%` : '—'} sub={`${fmtB(x.host.disk_hq?.free)} free · ${x.host.disk_hq?.path}`} tone={diskPct != null && diskPct > 90 ? 'warn' : undefined} />
            <Tile label="Uptime" value={fmtUp(x.host.uptime)} sub={`hermes-hq v${d?.version ?? '—'} · db ${d?.db_path ?? ''}`} />
          </div>
        </>
      )}
      <details className="mt-2"><summary className="cursor-pointer font-mono text-[10px] uppercase tracking-widest text-muted">Raw runtime</summary>
        <pre className="mt-2 overflow-x-auto rounded-xl border border-line bg-inset p-4 font-mono text-xs text-muted">{JSON.stringify({ system: d, stats: x }, null, 2)}</pre></details>
    </section>
  )
}

function SnapshotBanner() {
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()) })
  if (!sys.data?.imported_from || sys.data?.dispatcher?.enabled) return null
  return <div className="truncate border-b border-needsyou/30 bg-needsyou/10 px-4 py-1 text-center font-mono text-[9px] uppercase tracking-widest text-needsyou sm:text-[10px]" title={`Imported from ${sys.data.imported_from}; dispatcher off; writes here are not seen by the old Work Manager`}>Snapshot mode · dispatcher off · <span className="hidden sm:inline">imported from {sys.data.imported_from} · </span>writes are throwaway until cutover</div>
}

export default function App() {
  const qc = useQueryClient()
  const session = useSession()
  const [authed, setAuthed] = useState<boolean | null>(null)
  useEffect(() => { if (session.data?.csrf) { setCsrf(session.data.csrf); setAuthed(true) } else if (session.isError) setAuthed(false) }, [session.data, session.isError])
  useEffect(() => {
    const h = () => { setAuthed(false); qc.clear() }
    window.addEventListener('hq:unauthenticated', h); return () => window.removeEventListener('hq:unauthenticated', h)
  }, [qc])
  if (authed === null) return null
  if (!authed) return <LoginScreen onDone={() => { setAuthed(true); qc.invalidateQueries() }} />
  return (
    <ToastProvider>
      <div className="min-h-full">
        <TopBar />
        <SnapshotBanner />
        <main>
          <Routes>
            {[...TABS, ...TOOLS].filter(([, to]) => !['/projects', '/tasks', '/', '/activity'].includes(to)).map(([label, to]) => <Route key={to} path={to} element={<Placeholder title={label} />} />)}
            <Route path="/" element={<Overview />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/projects/:slug" element={<ProjectDetail />} />
            <Route path="/tasks" element={<Tasks />} />
            <Route path="/tasks/:id" element={<TaskDetail />} />
            <Route path="/system" element={<System />} />
          </Routes>
        </main>
      </div>
    </ToastProvider>
  )
}
