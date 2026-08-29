import clsx from 'clsx'
import { HUMAN_LABEL, toHuman, type Human } from '../status'

const COLOR = {
  backlog: 'bg-backlog/15 text-backlog border-backlog/40',
  queued: 'bg-queued/15 text-queued border-queued/40',
  working: 'bg-working/15 text-working border-working/40',
  needsyou: 'bg-needsyou/15 text-needsyou border-needsyou/40',
  done: 'bg-done/15 text-done border-done/40',
}

export function StatusBadge({ status, human, reason, compact, live }: { status?: string; human?: Human; reason?: string; compact?: boolean; live?: boolean }) {
  const h = human ?? toHuman(status ?? '')
  const { state } = h
  const note = compact ? undefined : (reason ?? h.reason)
  return (
    <span className={clsx('inline-flex max-w-full items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium', COLOR[state])}>
      <span className={clsx('size-1.5 rounded-full bg-current', (live || status === 'running') && 'hq-dot-live')} />
      {HUMAN_LABEL[state]}
      {note && <span className="max-w-[14rem] truncate opacity-70" title={note}>· {note}</span>}
    </span>
  )
}
