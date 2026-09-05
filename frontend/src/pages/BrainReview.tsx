/** Second Brain Review queue (Phase 2a): the owner's side of the librarian
 * loop. Every Library change the librarian wants lives here as a proposal —
 * approve applies it, reject sends written feedback back to the librarian.
 * Nothing changes without a click on this screen. */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { ago, post, useAreas, useProjects, useProposals, type Proposal, type SplitPart } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { BrainSubNav, areaLabel, useBrainCounts } from '../components/brain'
import { Btn, ConfirmModal, Field, Modal, TextArea } from '../components/Modal'
import { Chip, Empty, Label, Loading, Select } from '../components/ui'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'

const KIND_TONE: Record<Proposal['kind'], string> = { split: 'text-accent-2', file: 'text-queued' }

export function BrainReview() {
  usePageTitle('Review — Second Brain')
  const qc = useQueryClient(); const toast = useToast()
  const { inbox, review } = useBrainCounts()
  const [status, setStatus] = useState('pending')
  const proposals = useProposals(status)
  const areas = useAreas(); const projects = useProjects()
  const [busy, setBusy] = useState<number | 'routine' | null>(null)
  const [rejecting, setRejecting] = useState<Proposal | null>(null)
  const [confirmRoutine, setConfirmRoutine] = useState(false)

  const act = async (key: number | 'routine', fn: () => Promise<unknown>, ok: string) => {
    setBusy(key)
    try { await fn(); toast(ok); qc.invalidateQueries() }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
    finally { setBusy(null) }
  }
  const counts = proposals.data?.counts
  const rows = proposals.data?.proposals ?? []
  const destChips = (p: { area_id?: number; project_id?: number; tags?: string[]; type?: string }) => {
    const area = areaLabel(areas.data?.areas, p.area_id ?? null)
    const proj = p.project_id ? (projects.data?.projects ?? []).find(x => x.id === p.project_id) : null
    return (
      <>
        {area && <Chip tone="accent">{area}</Chip>}
        {proj && <Chip tone="accent">{proj.name}</Chip>}
        {p.type && p.type !== 'note' && <Chip>{p.type}</Chip>}
        {(p.tags ?? []).map(t => <Chip key={t}>{t}</Chip>)}
        {!area && !proj && <Chip>→ inbox</Chip>}
      </>
    )
  }
  return (
    <section className="mx-auto max-w-4xl p-4 sm:p-6">
      {rejecting && <RejectModal p={rejecting} onClose={() => setRejecting(null)} />}
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
              <span className={clsx('font-mono text-[10px] uppercase tracking-wider', KIND_TONE[p.kind])}>{p.kind}</span>
              {p.classification === 'needs_attention'
                ? <span className="inline-flex items-center rounded-full border border-needsyou/60 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-needsyou">needs attention</span>
                : <Chip>routine</Chip>}
              <span className="min-w-0 flex-1" />
              <span className="font-mono text-[10px] text-muted">#{p.id} · {ago(p.created_at)}</span>
            </div>
            <p className="mt-2 text-sm">
              <Link to={`/brain/note/${p.note_id}`} className="font-medium hover:text-accent-2">{p.note_title ?? `note #${p.note_id}`}</Link>
              {p.note_status === 'archived' && <span className="ml-2 text-[11px] text-muted">(archived)</span>}
            </p>
            {p.summary && <p className="mt-1 text-xs text-muted">{p.summary}</p>}
            {p.kind === 'file' && p.payload && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5">{destChips(p.payload)}</div>
            )}
            {p.kind === 'split' && (
              <ol className="mt-2 flex flex-col gap-1.5">
                {(p.payload?.parts ?? []).map((part: SplitPart, i: number) => (
                  <li key={i} className="rounded-lg border border-line-subtle bg-inset px-3 py-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-xs font-medium">{part.title}</span>
                      {destChips(part)}
                    </div>
                    {part.body && <p className="mt-1 line-clamp-2 text-[11px] text-muted">{part.body}</p>}
                  </li>
                ))}
                {p.payload?.archive_original === false && <p className="text-[11px] text-muted">Source note stays (not archived).</p>}
              </ol>
            )}
            {p.status === 'pending' ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <Btn busy={busy === p.id} onClick={() => void act(p.id, () => post(`/api/proposal/${p.id}/approve`),
                  p.kind === 'split' ? 'Split applied' : 'Filed')}>
                  {p.kind === 'split' ? `Approve split (${p.payload?.parts?.length ?? 0} notes)` : 'Approve filing'}
                </Btn>
                <Btn kind="ghost" onClick={() => setRejecting(p)}>Reject…</Btn>
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                <Chip>{p.status}</Chip>
                {p.decided_at && <span>{ago(p.decided_at)}</span>}
                {p.status === 'approved' && p.result?.note_ids && <span>created {p.result.note_ids.length} note{p.result.note_ids.length === 1 ? '' : 's'}</span>}
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
    <Modal title={`Reject ${p.kind} proposal #${p.id}`} onClose={onClose}>
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

export default BrainReview
