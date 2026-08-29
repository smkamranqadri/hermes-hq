import { Link, useParams } from 'react-router-dom'
import { useAgent, ago, when } from '../api'
import { GlassCard } from '../components/GlassCard'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, Loading, Chip, Crumbs, Label } from '../components/ui'
import { ActionBtn } from '../components/forms'
import { usePageTitle } from '../usePageTitle'
import { GatewayDot } from './Agents'

export function AgentDetail() {
  const name = useParams().name ?? ''
  const q = useAgent(name)
  const a = q.data
  usePageTitle(a ? `Agent ${a.name}` : 'Agent')
  if (q.isLoading) return <section className="mx-auto max-w-6xl p-4 sm:p-6"><Loading rows={1} /><div className="mt-4 grid gap-4 lg:grid-cols-2"><Loading rows={3} /><Loading rows={3} /></div></section>
  if (q.isError || !a) return <section className="mx-auto max-w-6xl p-6"><Empty error title={`Could not load /api/agent/${name}`} note={String(q.error ?? '404')} /></section>
  const g = a.gateway
  const isDefault = a.name === 'orchestrator'
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <Crumbs items={[['Agents', '/agents'], [a.name]]} />
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-mono text-lg font-semibold text-accent-2 sm:text-xl">{a.name}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">{a.description}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
            {!a.installed && <Chip>not installed</Chip>}{isDefault && <Chip>default profile</Chip>}{isDefault && <Chip tone={a.overlay_applied ? 'accent' : 'muted'}>{a.overlay_applied ? 'HQ soul' : 'stock soul'}</Chip>}
            <span className="break-all font-mono text-[10px]">{a.home}</span>
          </div>
        </div>
        <div className="flex basis-full flex-wrap items-center gap-2">
          <GatewayDot g={g} />
          {!isDefault && a.installed && (g.enabled
            ? <ActionBtn url={`/api/agent/${a.name}/gateway`} label="Disable chat" kind="ghost" body={{ enabled: false }} confirm={`Stop the ${a.name} gateway? Open chats will drop.`} />
            : <ActionBtn url={`/api/agent/${a.name}/gateway`} label="Enable chat" body={{ enabled: true }} confirm={`Start the ${a.name} gateway on :${g.port ?? 'auto'}? Its .env gets API_SERVER_PORT/KEY if missing.`} />)}
          {isDefault && !a.overlay_applied && <ActionBtn url="/api/agents/install" label="Apply Orchestrator soul" kind="ghost" body={{ template: 'orchestrator' }} confirm="Overwrite the default profile's SOUL.md with the HQ Orchestrator soul? The current file is backed up next to it." />}
        </div>
      </div>
      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <GlassCard className="min-w-0 overflow-hidden">
          <Label>Runs · {a.runs} total · {a.runs_done} done · {a.runs_failed} failed</Label>
          {a.recent_runs.length === 0 && <p className="mt-2 text-xs text-muted">No runs yet.</p>}
          <ul className="mt-2 divide-y divide-line">
            {a.recent_runs.map(r => (
              <li key={r.id} className="flex min-w-0 items-center gap-3 py-2 text-xs">
                <span className="w-12 shrink-0 font-mono text-muted">#{r.id}</span>
                <StatusBadge status={r.status} compact />
                <span className="min-w-0 flex-1 truncate">{r.task_id ? <Link to={`/tasks/${r.task_id}`} className="hover:underline">#{r.task_id} {r.task_title}</Link> : '—'}</span>
                <span className="shrink-0 text-muted">{ago(r.started_at)}</span>
              </li>
            ))}
          </ul>
        </GlassCard>
        <GlassCard className="min-w-0 overflow-hidden">
          <Label>Sessions · {a.sessions} total · {a.recent_sessions.length} recent</Label>
          {a.recent_sessions.length === 0 && <p className="mt-2 text-xs text-muted">No sessions in this profile's state.db.</p>}
          <ul className="mt-2 divide-y divide-line">
            {a.recent_sessions.map(s => (
              <li key={s.id} className="min-w-0 py-2 text-xs">
                <div className="flex min-w-0 items-center gap-2"><span className="min-w-0 flex-1 truncate">{s.title || s.id}</span><span className="shrink-0 text-muted">{ago(s.last_activity_at ?? s.started_at)}</span></div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[10px] text-muted"><span>{s.id.slice(0, 8)}</span>{s.model && <span>{s.model}</span>}{s.message_count != null && <span>{s.message_count} msgs</span>}{s.estimated_cost_usd != null && <span>${s.estimated_cost_usd.toFixed(2)}</span>}{s.source && <span>{s.source}</span>}</div>
              </li>
            ))}
          </ul>
          {a.last_active_at && <p className="mt-2 text-[11px] text-muted">last active {when(a.last_active_at)}</p>}
        </GlassCard>
      </div>
    </section>
  )
}
