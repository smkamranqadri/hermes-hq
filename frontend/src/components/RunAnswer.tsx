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

export function RunAnswer({ runId, blocked = false, compact = false }: {
  runId: number; blocked?: boolean; compact?: boolean
}) {
  const qc = useQueryClient(); const toast = useToast()
  const q = useRunQuestion(runId)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const question: RunQuestion | null = q.data?.question ?? null
  const multi = question?.mode === 'multi'
  const [picked, setPicked] = useState<string[]>([])
  // A live run with nothing asked has nothing to answer — stay out of the way.
  // A blocked run always gets a box: it stopped for the owner either way.
  if (!question && !blocked) return null

  const send = async (message: string, key: string) => {
    setBusy(key)
    try {
      const r = await post<{ resumed?: boolean }>(`/api/run/${runId}/answer`, { message })
      toast(r?.resumed ? 'Answer delivered — the same run has resumed'
        : 'Answer delivered — the agent picks it up on its next step')
      setText(''); setPicked([]); qc.invalidateQueries()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(null) }
  }
  const toggle = (label: string) => setPicked(p =>
    p.includes(label) ? p.filter(x => x !== label) : multi ? [...p, label] : [label])

  return (
    <div className={`flex basis-full flex-col gap-2 rounded-lg border border-needsyou/50 bg-needsyou/10 p-3 ${compact ? 'text-sm' : ''}`} data-run-answer>
      {question?.question && <p className="break-words text-sm font-medium">{question.question}</p>}
      {!!question?.options.length && (
        <div className="flex flex-wrap gap-2">
          {question.options.map(o => (
            <button key={o.label} type="button" title={o.detail || undefined}
              disabled={!!busy}
              onClick={() => multi ? toggle(o.label) : void send(o.label, o.label)}
              className={`rounded-full border px-3 py-1 text-left text-[13px] disabled:opacity-60 ${
                picked.includes(o.label)
                  ? 'border-needsyou bg-needsyou/25 text-fg'
                  : 'border-needsyou/60 text-needsyou hover:bg-needsyou/20'}`}>
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
      <p className="text-[11px] text-muted">
        {blocked
          ? 'The run stopped waiting — answering resumes the same run.'
          : 'The run is live: your answer lands in its answer file and it continues in the same session.'}
      </p>
    </div>
  )
}
