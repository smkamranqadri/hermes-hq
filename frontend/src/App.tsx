import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { TopBar, TABS, TOOLS } from './components/TopBar'
import { StatusBadge } from './components/StatusBadge'
import { GlassCard, PageHeader } from './components/GlassCard'
import { ToastProvider } from './components/Toast'
import { LoginScreen } from './components/LoginScreen'
import { ActionBtn } from './components/forms'
import { Btn } from './components/Modal'
import { post, setCsrf, useSession } from './api'
import { usePageTitle } from './usePageTitle'
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

function System() {
  usePageTitle('System')
  const qc = useQueryClient()
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()), refetchInterval: 5000 })
  const d = sys.data
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="system" title="System" right={<Btn kind="ghost" onClick={async () => { await post('/api/logout'); window.dispatchEvent(new Event('hq:unauthenticated')) }}>Sign out</Btn>} />
      <div className="grid gap-4 md:grid-cols-2">
        <GlassCard accent="var(--hq-accent)">
          <h2 className="text-sm font-semibold">Dispatcher</h2>
          <p className="mt-1 text-xs text-muted">
            {d?.dispatcher?.enabled ? (d?.dispatcher?.alive ? 'loop alive' : 'loop not running') : 'disabled for this server process (--no-dispatcher)'}
            {' · '}{d?.paused ? 'PAUSED — nothing will be claimed' : 'active'}
            {d?.dispatcher?.last_tick ? ` · last tick ${new Date(d.dispatcher.last_tick * 1000).toLocaleTimeString()}` : ''}
          </p>
          {d?.dispatcher?.last_error && <p className="mt-1 text-xs text-error">{d.dispatcher.last_error}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            {d?.paused
              ? <ActionBtn url="/api/system/resume" label="Resume" confirm="Resume dispatching? Ready tasks will be claimed and agents launched." onDone={() => qc.invalidateQueries()} />
              : <ActionBtn url="/api/system/pause" label="Pause" kind="warn" confirm="Pause the dispatcher? Running agents finish; nothing new starts." onDone={() => qc.invalidateQueries()} />}
            <ActionBtn url="/api/system/dispatch" label="Tick now" kind="ghost" confirm="Run one dispatcher tick now?" onDone={() => qc.invalidateQueries()} />
          </div>
        </GlassCard>
        <GlassCard>
          <h2 className="text-sm font-semibold">Status primitive</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {['planned', 'ready', 'running', 'needs_review', 'waiting_approval', 'blocked', 'failed', 'done'].map(s => <StatusBadge key={s} status={s} />)}
          </div>
        </GlassCard>
        <GlassCard className="md:col-span-2">
          <h2 className="text-sm font-semibold">Runtime</h2>
          <pre className="mt-3 overflow-x-auto font-mono text-xs text-muted">{JSON.stringify(d, null, 2)}</pre>
        </GlassCard>
      </div>
    </section>
  )
}

function SnapshotBanner() {
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()) })
  if (!sys.data?.imported_from || sys.data?.dispatcher?.enabled) return null
  return <div className="border-b border-needsyou/30 bg-needsyou/10 px-4 py-1 text-center font-mono text-[10px] uppercase tracking-widest text-needsyou">Snapshot mode · imported from {sys.data.imported_from} · dispatcher off · writes here are not seen by the old Work Manager</div>
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
            {[...TABS, ...TOOLS].filter(([, to]) => to !== '/projects' && to !== '/tasks').map(([label, to]) => <Route key={to} path={to} element={<Placeholder title={label} />} />)}
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
