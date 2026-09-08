/** Second Brain Review queue (Phase 2a/2b): the owner's side of the librarian
 * loop. Every Library change the librarian wants lives here as a proposal —
 * approve applies it (optionally after editing the payload), reject sends
 * written feedback back to the librarian. Nothing changes without a click on
 * this screen. */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { ago, post, useAreas, useProjects, useProposals, useRoster, type Proposal, type ProposalPayload, type SplitPart } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { BrainSubNav, EDITABLE, EditProposalModal, KIND_LABEL, ProposalPayloadView, approveLabel, pillCls, useBrainCounts } from '../components/brain'
import { Btn, ConfirmModal, Field, Modal, SelectInput, TextArea, TextInput } from '../components/Modal'
import { Chip, Empty, Loading, Select } from '../components/ui'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'

const KIND_TONE: Record<Proposal['kind'], string> = {
  split: 'text-accent-2', file: 'text-queued', contradiction: 'text-needsyou', new_task: 'text-working',
  append_entry: 'text-accent-2',
}

export function BrainReview() {
  usePageTitle('Review — Second Brain')
  const qc = useQueryClient(); const toast = useToast()
  const { inbox, review } = useBrainCounts()
  const [status, setStatus] = useState('pending')
  const proposals = useProposals(status)
  const [busy, setBusy] = useState<number | 'routine' | null>(null)
  const [rejecting, setRejecting] = useState<Proposal | null>(null)
  const [editing, setEditing] = useState<Proposal | null>(null)
  const [confirmRoutine, setConfirmRoutine] = useState(false)

  const act = async (key: number | 'routine', fn: () => Promise<unknown>, ok: string) => {
    setBusy(key)
    try { await fn(); toast(ok); qc.invalidateQueries() }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
    finally { setBusy(null) }
  }
  const approvedToast = (p: Proposal) => ({
    split: 'Split applied', file: p.payload?.archive ? 'Archived' : 'Filed',
    contradiction: 'Both notes flagged disputed', new_task: 'Task created & linked',
    append_entry: `Thought added to note #${p.payload?.target_note_id ?? '?'}`,
  } satisfies Record<Proposal['kind'], string>)[p.kind]
  const counts = proposals.data?.counts
  const rows = proposals.data?.proposals ?? []
  return (
    <section className="mx-auto max-w-4xl p-4 sm:p-6">
      {rejecting && <RejectModal p={rejecting} onClose={() => setRejecting(null)} />}
      {editing && <EditProposalModal p={editing} onClose={() => setEditing(null)} />}
      {confirmRoutine && (
        <ConfirmModal
          title={`Approve ${counts?.routine ?? 0} routine proposals`}
          message="Applies every pending proposal the librarian marked routine. Needs-attention items stay for you."
          confirmLabel="Approve all routine" onClose={() => setConfirmRoutine(false)}
          onConfirm={() => { setConfirmRoutine(false); void act('routine', () => post('/api/proposals/approve-routine'), 'Routine proposals approved') }}
        />
      )}
      <PageHeader crumb="second-brain / review" title="Review" right={<BrainSubNav inbox={inbox} review={review} />} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select value={status} onChange={e => setStatus(e.target.value)} aria-label="Status filter">
          {['pending', 'approved', 'rejected', 'superseded'].map(s => <option key={s}>{s}</option>)}
        </Select>
        {counts && status === 'pending' && (
          <span className="text-xs text-muted">{counts.needs_attention} need attention · {counts.routine} routine</span>
        )}
        {(counts?.routine ?? 0) > 0 && status === 'pending' && (
          <Btn className="ml-auto" busy={busy === 'routine'} onClick={() => setConfirmRoutine(true)}>
            Approve all routine ({counts?.routine})
          </Btn>
        )}
      </div>
      {proposals.isLoading && <Loading rows={4} />}
      {!proposals.isLoading && rows.length === 0 && (
        <Empty title={status === 'pending' ? 'Review queue is clear' : `No ${status} proposals`}
          note={status === 'pending' ? 'The librarian files proposals here after each ingest run. You approve everything.' : undefined} />
      )}
      <div className="flex flex-col gap-3">
        {rows.map(p => (
          <GlassCard key={p.id} className="p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={clsx('font-mono text-[10px] uppercase tracking-wider', KIND_TONE[p.kind])}>{KIND_LABEL[p.kind]}</span>
              {p.classification === 'needs_attention'
                ? <span className={pillCls('needsyou')}>needs attention</span>
                : <Chip>routine</Chip>}
              <span className="min-w-0 flex-1" />
              <span className="font-mono text-[10px] text-muted">#{p.id} · {ago(p.created_at)}</span>
            </div>
            <p className="mt-2 text-sm">
              <Link to={`/brain/note/${p.note_id}`} state={{ from: '/brain/review' }} className="font-medium hover:text-accent-2">{p.note_title ?? `note #${p.note_id}`}</Link>
              {p.note_status === 'archived' && <span className="ml-2 text-[11px] text-muted">(archived)</span>}
            </p>
            {p.summary && <p className="mt-1 text-xs text-muted">{p.summary}</p>}
            <ProposalPayloadView p={p} />
            {p.status === 'pending' ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <Btn busy={busy === p.id} onClick={() => void act(p.id, () => post(`/api/proposal/${p.id}/approve`), approvedToast(p))}>
                  {approveLabel(p)}
                </Btn>
                {EDITABLE[p.kind] && <Btn kind="ghost" onClick={() => setEditing(p)}>Edit…</Btn>}
                <Btn kind="ghost" onClick={() => setRejecting(p)}>Reject…</Btn>
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                <Chip>{p.status}</Chip>
                {p.decided_at && <span>{ago(p.decided_at)}</span>}
                {p.status === 'approved' && p.result?.note_ids && <span>created {p.result.note_ids.length} note{p.result.note_ids.length === 1 ? '' : 's'}</span>}
                {p.status === 'approved' && p.result?.archived && <span>sent to Archive</span>}
                {p.status === 'approved' && p.result?.disputed && <span>both notes flagged disputed</span>}
                {p.status === 'approved' && p.result?.task_id && (
                  <Link to={`/tasks/${p.result.task_id}`} className="text-accent-2 hover:underline">task #{p.result.task_id} →</Link>
                )}
                {p.feedback && <span className="w-full">Your feedback: {p.feedback}</span>}
              </div>
            )}
          </GlassCard>
        ))}
      </div>
    </section>
  )
}

/** Reject with written feedback — the librarian reads it before re-proposing. */
function RejectModal({ p, onClose }: { p: Proposal; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const [feedback, setFeedback] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await post(`/api/proposal/${p.id}/reject`, { feedback })
      toast('Rejected — feedback saved for the librarian'); qc.invalidateQueries(); onClose()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title={`Reject ${KIND_LABEL[p.kind]} proposal #${p.id}`} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Field label="Why (the librarian reads this)" hint="Optional but powerful — it steers the next proposal.">
          <TextArea rows={3} value={feedback} onChange={e => setFeedback(e.target.value)} placeholder="e.g. This belongs under Family, not Work." />
        </Field>
        <div className="flex justify-end gap-2">
          <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
          <Btn onClick={() => void save()} busy={busy}>Reject proposal</Btn>
        </div>
      </div>
    </Modal>
  )
}

/** Edit-before-approve: adjust the librarian's payload, then approve in one
 * step. The server re-validates and persists the edited payload on the row,
 * so the record shows what was actually approved. */

export default BrainReview
