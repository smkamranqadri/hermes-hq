import clsx from 'clsx'
import { HUMAN_LABEL, toHuman } from '../status'

const COLOR = {
  backlog: 'bg-backlog/15 text-backlog border-backlog/40',
  queued: 'bg-queued/15 text-queued border-queued/40',
  working: 'bg-working/15 text-working border-working/40',
  needsyou: 'bg-needsyou/15 text-needsyou border-needsyou/40',
  done: 'bg-done/15 text-done border-done/40',
}

export function StatusBadge({ status, reason }: { status: string; reason?: string }) {
  const { state, detail } = toHuman(status)
  const note = reason ?? detail
  return (
    <span className={clsx('inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium', COLOR[state])}>
      <span className="size-1.5 rounded-full bg-current" />
      {HUMAN_LABEL[state]}
      {note && <span className="opacity-70">· {note}</span>}
    </span>
  )
}
