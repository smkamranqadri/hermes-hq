// Mirror of backend/status.py HUMAN_STATE (tests/backend/test_status.py asserts agreement).
// The server sends `human: {state, reason, action}` on every task; this file
// only supplies labels/colors and a fallback when a raw engine status is shown.
export type HumanState = 'backlog' | 'queued' | 'working' | 'needsyou' | 'done'
export type Human = { state: HumanState; reason?: string | null; action?: string | null }

export const ORDER: HumanState[] = ['needsyou', 'working', 'queued', 'backlog', 'done']
export const HUMAN_LABEL: Record<HumanState, string> = {
  needsyou: 'Needs you', working: 'Working', queued: 'Queued', backlog: 'Backlog', done: 'Done',
}

export function toHuman(engineStatus: string): Human {
  switch (engineStatus) {
    case 'planned': return { state: 'backlog' }
    case 'draft': return { state: 'backlog' }
    case 'ready': return { state: 'queued' }
    case 'rework': return { state: 'queued', reason: 'rework requested' }
    case 'waiting_approval': return { state: 'queued' }
    case 'running': return { state: 'working' }
    case 'needs_review': return { state: 'working', reason: 'reviewer checking' }
    case 'blocked': return { state: 'needsyou', reason: 'blocked' }
    case 'failed': return { state: 'needsyou', reason: 'failed' }
    case 'stalled': return { state: 'needsyou', reason: 'stalled' }
    case 'done': return { state: 'done' }
    case 'manual': return { state: 'done' }
    default: return { state: 'backlog', reason: engineStatus }
  }
}
