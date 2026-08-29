import { Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { TopBar, TABS, TOOLS } from './components/TopBar'
import { StatusBadge } from './components/StatusBadge'
import { GlassCard, PageHeader } from './components/GlassCard'

function Placeholder({ title }: { title: string }) {
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb={title.toLowerCase()} title={title} />
      <GlassCard><p className="text-sm text-muted">Not built yet. Nothing here is simulated.</p></GlassCard>
    </section>
  )
}

function System() {
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()) })
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="system" title="System" />
      <div className="grid gap-4 md:grid-cols-2">
        <GlassCard accent="var(--hq-accent)">
          <h2 className="text-sm font-semibold">Runtime</h2>
          <pre className="mt-3 overflow-x-auto font-mono text-xs text-muted">{JSON.stringify(sys.data, null, 2)}</pre>
        </GlassCard>
        <GlassCard>
          <h2 className="text-sm font-semibold">Status primitive</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {['planned', 'ready', 'running', 'needs_review', 'waiting_approval', 'blocked', 'failed', 'done'].map(s => <StatusBadge key={s} status={s} />)}
          </div>
        </GlassCard>
      </div>
    </section>
  )
}

export default function App() {
  return (
    <div className="min-h-full">
      <TopBar />
      <main>
        <Routes>
          {[...TABS, ...TOOLS].map(([label, to]) => <Route key={to} path={to} element={<Placeholder title={label} />} />)}
          <Route path="/system" element={<System />} />
        </Routes>
      </main>
    </div>
  )
}
