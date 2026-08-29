import { Link } from 'react-router-dom'
import type { ActivityEvent } from '../api'
import { ago } from '../api'
import { Agent } from './ui'

export function ActivityList({ events, compact = false }: { events: ActivityEvent[]; compact?: boolean }) {
  if (!events.length) return <p className="text-xs text-muted">No activity yet.</p>
  return (
    <div className="glass divide-y divide-line-subtle rounded-xl">
      {events.map(e => (
        <div key={`${e.kind}-${e.id}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-3 py-2 text-xs sm:flex-nowrap">
          <span className="w-14 shrink-0 font-mono text-[10px] text-muted" title={new Date(e.ts * 1000).toLocaleString()}>{ago(e.ts)}</span>
          {e.kind === 'transition'
            ? <span className="shrink-0 font-mono text-[10px] text-queued">{e.action}</span>
            : <><Agent name={e.agent_profile} /><span className="shrink-0 font-mono text-[10px] text-muted">{e.action}</span></>}
          <span className="min-w-0 flex-1 truncate">
            {e.task_id && <Link to={`/tasks/${e.task_id}`} className="text-fg hover:text-accent-2">#{e.task_id}{!compact && e.task_title ? ` ${e.task_title}` : ''}</Link>}
            {e.detail && <span className="text-muted"> {e.task_id ? '· ' : ''}{e.detail}</span>}
          </span>
          {e.project_slug && !compact && <span className="shrink-0 font-mono text-[10px] text-muted">{e.project_slug}</span>}
        </div>
      ))}
    </div>
  )
}
