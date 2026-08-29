// Two-layer status: the engine keeps its precise state machine; the UI shows
// five human states plus a reason. See kis/knowledge/technical.md.
export type HumanState = 'backlog' | 'queued' | 'working' | 'needsyou' | 'done'

export const HUMAN_LABEL: Record<HumanState, string> = {
  backlog: 'Backlog', queued: 'Queued', working: 'Working', needsyou: 'Needs you', done: 'Done',
}

export function toHuman(engineStatus: string): { state: HumanState; detail?: string } {
  switch (engineStatus) {
    case 'planned': case 'draft': return { state: 'backlog' }
    case 'ready': return { state: 'queued' }
    case 'running': return { state: 'working' }
    case 'needs_review': return { state: 'working', detail: 'reviewer checking' }
    case 'rework': return { state: 'queued', detail: 'rework requested' }
    case 'waiting_approval': return { state: 'needsyou', detail: 'approval' }
    case 'blocked': return { state: 'needsyou', detail: 'blocked' }
    case 'failed': return { state: 'needsyou', detail: 'failed' }
    case 'stalled': return { state: 'needsyou', detail: 'stalled' }
    case 'done': case 'manual': return { state: 'done' }
    default: return { state: 'backlog', detail: engineStatus }
  }
}
