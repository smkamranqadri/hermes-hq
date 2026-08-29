import { Link } from 'react-router-dom'
import type { Task } from '../api'
import { ago } from '../api'
import { StatusBadge } from './StatusBadge'
import { Agent, Chip } from './ui'

export function TaskRow({ t, showProject = true, stacked = false }: { t: Task; showProject?: boolean; stacked?: boolean }) {
  const row = stacked ? 'flex-col gap-2' : 'flex-col gap-2 sm:flex-row sm:items-center sm:gap-4'
  return (
    <Link to={`/tasks/${t.id}`} className={`glass flex rounded-xl px-4 py-3 transition hover:bg-raised ${row}`}>
      <span className={`font-mono text-xs text-muted ${stacked ? '' : 'sm:w-12'}`}>#{t.id}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{t.title}</span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
          <Agent name={t.assignee_profile} />
          {showProject && <span>· {t.project_slug}</span>}
          {t.goal_title && <span className="truncate">· {t.goal_title}</span>}
          {!!t.is_code && <Chip>code</Chip>}
        </span>
        {t.human.reason && t.human.reason.length > 40 && (
          <span className="mt-1 line-clamp-2 text-[11px] text-needsyou/90">{t.human.reason}</span>
        )}
      </span>
      <span className={`flex shrink-0 flex-wrap items-center gap-3 ${stacked ? '' : 'sm:justify-end'}`}>
        <StatusBadge human={{ ...t.human, reason: t.human.reason && t.human.reason.length > 40 ? t.human.reason.split(':')[0] : t.human.reason }} />
        <span className="font-mono text-[10px] text-muted">{ago(t.updated_at ?? t.created_at)}</span>
      </span>
    </Link>
  )
}
