/** Answering an agent's question (intent/OwnerQuestionLoopPlan.md, Slice A).
 *
 * The agent asks with an ```hq-options fence and polls its answer file between
 * steps, so answering a RUNNING run continues the SAME session — no relaunch.
 * A blocked run's answer resumes the same run id instead. One component serves
 * both, and both surfaces (task detail, inbox) use it so the wording and the
 * options can never drift apart.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { post, useRunQuestion, type RunQuestion } from '../api'
import { Btn, TextArea } from './Modal'
import { useToast } from './Toast'

export function RunAnswer({ runId, blocked = false, compact = false, fallbackQuestion }: {
  runId: number; blocked?: boolean; compact?: boolean
  /** What the run stopped on when it never used an hq-options fence — its
   * blocker IS the question, and it must be readable IN FULL here (the
   * board's one-line reason is truncated to 120 chars by design). */
  fallbackQuestion?: string | null
}) {
  const qc = useQueryClient(); const toast = useToast()
  const q = useRunQuestion(runId)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const question: RunQuestion | null = q.data?.question ?? null
  const multi = question?.mode === 'multi'
  const [picked, setPicked] = useState<string[]>([])
  // What actually reached the run. A toast is easy to miss on a phone, and
  // without a persistent receipt the owner cannot tell a delivered answer from
  // a dead button — so they tap again, and every tap appends another line to
  // the answer file. Seen live 2026-09-07 (run 466 received C,C,C,C,B).
  const [sent, setSent] = useState<{ message: string; resumed: boolean } | null>(null)
  // A live run with nothing asked has nothing to answer — stay out of the way.
  // A blocked run always gets a box: it stopped for the owner either way.
  if (!question && !blocked) return null
  const asked = question?.question || (fallbackQuestion || '').trim()

  const send = async (message: string, key: string) => {
    setBusy(key)
    try {
      const r = await post<{ resumed?: boolean }>(`/api/run/${runId}/answer`, { message })
      toast(r?.resumed ? 'Answer delivered — the same run has resumed'
        : 'Answer delivered — the agent picks it up on its next step')
      // Keep `picked` — it is the on-screen record of what was chosen. Only the
      // free-text box is cleared, because its content is now in the receipt.
      setSent({ message, resumed: !!r?.resumed })
      setText(''); qc.invalidateQueries()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(null) }
  }
  const toggle = (label: string) => setPicked(p =>
    p.includes(label) ? p.filter(x => x !== label) : multi ? [...p, label] : [label])

  return (
    <div className={`flex basis-full flex-col gap-2 rounded-lg border border-needsyou/50 bg-needsyou/10 p-3 ${compact ? 'text-sm' : ''}`} data-run-answer>
      {asked && <p className="whitespace-pre-wrap break-words text-sm font-medium">{asked}</p>}
      {!!question?.options.length && (
        <div className="flex flex-wrap gap-2">
          {question.options.map(o => (
            <button key={o.label} type="button" title={o.detail || undefined}
              disabled={!!busy} aria-pressed={picked.includes(o.label)}
              // Single mode used to send WITHOUT recording the choice, so the
              // selected styling below was dead code and every tap looked
              // identical to no tap. Record first, then send.
              onClick={() => { if (multi) { toggle(o.label) } else { setPicked([o.label]); void send(o.label, o.label) } }}
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-left text-[13px] disabled:opacity-60 ${
                picked.includes(o.label)
                  ? 'border-needsyou bg-needsyou/25 text-fg'
                  : 'border-needsyou/60 text-needsyou hover:bg-needsyou/20'}`}>
              {busy === o.label && <span className="inline-block size-3 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent" />}
              {o.label}
              {o.detail && <span className="ml-2 text-[11px] text-muted">{o.detail}</span>}
            </button>
          ))}
        </div>
      )}
      {multi && picked.length > 0 && (
        <Btn busy={busy === 'multi'} onClick={() => void send(picked.join(', '), 'multi')}>
          Send {picked.length} choice{picked.length === 1 ? '' : 's'}
        </Btn>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <TextArea rows={2} value={text} onChange={e => setText(e.target.value)}
          placeholder={question?.options.length ? '…or answer in your own words' : "Answer the agent's question…"}
          className="min-w-[14rem] flex-1" />
        <Btn busy={busy === 'text'} disabled={!text.trim()} onClick={() => void send(text.trim(), 'text')}>
          {blocked ? 'Answer & Resume' : 'Send answer'}
        </Btn>
      </div>
      {sent && (
        <p data-answer-receipt className="rounded border border-done/40 bg-done/10 px-2 py-1 text-[11px] text-fg">
          <span aria-hidden="true">✓ </span>
          Sent “{sent.message.length > 80 ? sent.message.slice(0, 80) + '…' : sent.message}”
          {sent.resumed ? ' — the same run has resumed.' : ' — the agent picks it up on its next step.'}
          {' '}Answering again appends another line; the agent reads them all, so say
          plainly if you are changing your mind.
        </p>
      )}
      <p className="text-[11px] text-muted">
        {blocked
          ? 'The run stopped waiting — answering resumes the same run.'
          : 'The run is live: your answer lands in its answer file and it continues in the same session.'}
      </p>
    </div>
  )
}
