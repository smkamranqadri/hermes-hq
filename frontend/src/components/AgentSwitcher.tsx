// Profile picker shared by the Group 6 browsers: Orchestrator + every specialist profile on disk.
import { useQuery } from '@tanstack/react-query'
import { get } from '../api'

export type MemProfile = { name: string; home: string; exists: boolean }
export const useMemProfiles = () => useQuery({ queryKey: ['memory-profiles'], queryFn: () => get<{ profiles: MemProfile[] }>('/api/memory/profiles'), staleTime: 60000 })

export function AgentSwitcher({ value, onChange, className }: { value: string; onChange: (p: string) => void; className?: string }) {
  const q = useMemProfiles()
  return (
    <select value={value} onChange={e => onChange(e.target.value)} aria-label="Agent"
      className={`hq-select min-w-0 appearance-none truncate rounded-lg border border-line bg-inset py-1.5 pl-2 pr-8 text-sm outline-none focus:border-accent ${className ?? ''}`}>
      {q.data?.profiles.map(p => <option key={p.name} value={p.name} disabled={!p.exists}>{p.name === 'orchestrator' ? 'Orchestrator' : p.name}{p.exists ? '' : ' — no home'}</option>) ?? <option value={value}>{value}</option>}
    </select>
  )
}
