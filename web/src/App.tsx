import { Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { TopBar, TABS } from './components/TopBar'
import { StatusBadge } from './components/StatusBadge'

function Placeholder({ title }: { title: string }) {
  return (
    <section className="p-6">
      <p className="text-[10px] uppercase tracking-widest text-muted">hermes-hq // {title.toLowerCase()}</p>
      <h1 className="mt-1 text-xl font-semibold">{title}</h1>
      <p className="mt-4 text-sm text-muted">Not built yet. Nothing here is simulated.</p>
    </section>
  )
}

function System() {
  const sys = useQuery({ queryKey: ['system'], queryFn: () => fetch('/api/system').then(r => r.json()) })
  return (
    <section className="p-6">
      <p className="text-[10px] uppercase tracking-widest text-muted">hermes-hq // system</p>
      <h1 className="mt-1 text-xl font-semibold">System</h1>
      <pre className="mt-4 overflow-x-auto rounded border border-line bg-panel p-4 text-xs">{JSON.stringify(sys.data, null, 2)}</pre>
      <h2 className="mt-6 text-sm font-medium text-muted">Status primitive</h2>
      <div className="mt-2 flex flex-wrap gap-2">
        {['planned', 'ready', 'running', 'needs_review', 'waiting_approval', 'blocked', 'failed', 'done'].map(s => <StatusBadge key={s} status={s} />)}
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
          {TABS.map(([label, to]) => <Route key={to} path={to} element={<Placeholder title={label} />} />)}
          <Route path="/system" element={<System />} />
        </Routes>
      </main>
    </div>
  )
}
