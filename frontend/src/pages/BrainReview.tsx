/** Second Brain Review queue (Phase 2a/2b): the owner's side of the librarian
 * loop. Every Library change the librarian wants lives here as a proposal —
 * approve applies it (optionally after editing the payload), reject sends
 * written feedback back to the librarian. Nothing changes without a click on
 * this screen. */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { ago, post, useAreas, useProjects, useProposals, useRoster, type Area, type Proposal, type ProposalPayload, type SplitPart } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { BrainSubNav, KIND_LABEL, ProposalPayloadView, approveLabel, useBrainCounts } from '../components/brain'
import { Btn, ConfirmModal, Field, Modal, SelectInput, TextArea, TextInput } from '../components/Modal'
import { Chip, Empty, Loading, Select } from '../components/ui'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'

const KIND_TONE: Record<Proposal['kind'], string> = {
  split: 'text-accent-2', file: 'text-queued', contradiction: 'text-needsyou', new_task: 'text-working',
}
const EDITABLE_KINDS: Proposal['kind'][] = ['file', 'split', 'new_task']

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
  const approvedToast = (p: Proposal) =>
    p.kind === 'split' ? 'Split applied' : p.kind === 'file' ? (p.payload?.archive ? 'Archived' : 'Filed')
      : p.kind === 'contradiction' ? 'Both notes flagged disputed' : 'Task created & linked'
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
            <ProposalPayloadView p={p} />
            {p.status === 'pending' ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <Btn busy={busy === p.id} onClick={() => void act(p.id, () => post(`/api/proposal/${p.id}/approve`), approvedToast(p))}>
                  {approveLabel(p)}
                </Btn>
                {EDITABLE_KINDS.includes(p.kind) && <Btn kind="ghost" onClick={() => setEditing(p)}>Edit…</Btn>}
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

const tagsFrom = (s: string) => s.split(',').map(t => t.trim()).filter(Boolean)

function AreaSelect({ value, onChange, areas }: { value: string; onChange: (v: string) => void; areas: Area[] }) {
  const roots = areas.filter(a => !a.parent_id)
  return (
    <SelectInput value={value} onChange={e => onChange(e.target.value)} aria-label="Area">
      <option value="">— no area —</option>
      {roots.map(r => (
        <optgroup key={r.id} label={r.name}>
          <option value={r.id}>{r.name}</option>
          {areas.filter(a => a.parent_id === r.id).map(a => <option key={a.id} value={a.id}>{r.name} / {a.name}</option>)}
        </optgroup>
      ))}
    </SelectInput>
  )
}

/** Edit-before-approve: adjust the librarian's payload, then approve in one
 * step. The server re-validates and persists the edited payload on the row,
 * so the record shows what was actually approved. */
function EditProposalModal({ p, onClose }: { p: Proposal; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const areas = useAreas(); const projects = useProjects(); const roster = useRoster()
  const pay = p.payload ?? {}
  const [file, setFile] = useState({
    area_id: pay.area_id ? String(pay.area_id) : '', project_id: pay.project_id ? String(pay.project_id) : '',
    type: pay.type ?? '', tags: (pay.tags ?? []).join(', '), archive: !!pay.archive,
  })
  const [task, setTask] = useState({
    title: pay.title ?? '', description: pay.description ?? '',
    project_id: pay.project_id ? String(pay.project_id) : '', assignee: pay.assignee ?? 'owner',
  })
  const [parts, setParts] = useState<SplitPart[]>(() => (pay.parts ?? []).map(x => ({ ...x })))
  const [keepOriginal, setKeepOriginal] = useState(pay.archive_original === false)
  const [busy, setBusy] = useState(false)
  const setPart = (i: number, patch: Partial<SplitPart>) => setParts(ps => ps.map((x, j) => j === i ? { ...x, ...patch } : x))
  const buildPayload = (): ProposalPayload => {
    if (p.kind === 'file') {
      const out: ProposalPayload = {}
      if (file.area_id) out.area_id = Number(file.area_id)
      if (file.project_id) out.project_id = Number(file.project_id)
      if (file.type) out.type = file.type as ProposalPayload['type']
      if (file.tags.trim()) out.tags = tagsFrom(file.tags)
      if (file.archive) out.archive = true
      return out
    }
    if (p.kind === 'new_task') {
      const out: ProposalPayload = { title: task.title.trim() }
      if (task.description.trim()) out.description = task.description.trim()
      if (task.project_id) out.project_id = Number(task.project_id)
      if (task.assignee) out.assignee = task.assignee
      return out
    }
    return { parts, archive_original: !keepOriginal }
  }
  const save = async () => {
    setBusy(true)
    try {
      await post(`/api/proposal/${p.id}/approve`, { payload: buildPayload() })
      toast('Approved with your edits'); qc.invalidateQueries(); onClose()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  const projectOptions = (value: string, onChange: (v: string) => void) => (
    <SelectInput value={value} onChange={e => onChange(e.target.value)} aria-label="Project">
      <option value="">— no project —</option>
      {(projects.data?.projects ?? []).map(x => <option key={x.id} value={x.id}>{x.name}</option>)}
    </SelectInput>
  )
  return (
    <Modal title={`Edit ${KIND_LABEL[p.kind]} proposal #${p.id}`} onClose={onClose}>
      <div className="flex flex-col gap-3">
        {p.kind === 'file' && (
          <>
            <Field label="Area"><AreaSelect value={file.area_id} onChange={v => setFile(x => ({ ...x, area_id: v }))} areas={areas.data?.areas ?? []} /></Field>
            <Field label="Project">{projectOptions(file.project_id, v => setFile(x => ({ ...x, project_id: v })))}</Field>
            <Field label="Tags" hint="Comma-separated."><TextInput value={file.tags} onChange={e => setFile(x => ({ ...x, tags: e.target.value }))} /></Field>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={file.archive} onChange={e => setFile(x => ({ ...x, archive: e.target.checked }))} />
              Send to Archive (junk/museum — stays searchable)
            </label>
          </>
        )}
        {p.kind === 'new_task' && (
          <>
            <Field label="Title"><TextInput value={task.title} onChange={e => setTask(x => ({ ...x, title: e.target.value }))} /></Field>
            <Field label="Description"><TextArea rows={2} value={task.description} onChange={e => setTask(x => ({ ...x, description: e.target.value }))} /></Field>
            <Field label="Project" hint="Defaults to the note's project when left empty.">{projectOptions(task.project_id, v => setTask(x => ({ ...x, project_id: v })))}</Field>
            <Field label="Assignee" hint="owner = your own todo; agents get dispatched.">
              <SelectInput value={task.assignee} onChange={e => setTask(x => ({ ...x, assignee: e.target.value }))}>
                {(roster.data?.assignees ?? ['owner']).map(a => <option key={a}>{a}</option>)}
              </SelectInput>
            </Field>
          </>
        )}
        {p.kind === 'split' && (
          <>
            <div className="flex max-h-[50vh] flex-col gap-2 overflow-y-auto pr-1">
              {parts.map((part, i) => (
                <div key={i} className="rounded-lg border border-line-subtle bg-inset p-2">
                  <div className="flex items-center gap-2">
                    <TextInput value={part.title} onChange={e => setPart(i, { title: e.target.value })} aria-label={`Part ${i + 1} title`} />
                    {parts.length > 1 && (
                      <button type="button" title="Drop this part" className="shrink-0 text-muted hover:text-needsyou"
                        onClick={() => setParts(ps => ps.filter((_, j) => j !== i))}>✕</button>
                    )}
                  </div>
                  {part.body && <p className="mt-1 line-clamp-1 text-[11px] text-muted">{part.body}</p>}
                  <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                    <div className="min-w-0 flex-1"><AreaSelect value={part.area_id ? String(part.area_id) : ''} onChange={v => setPart(i, { area_id: v ? Number(v) : undefined })} areas={areas.data?.areas ?? []} /></div>
                    <div className="min-w-0 flex-1"><TextInput value={(part.tags ?? []).join(', ')} onChange={e => setPart(i, { tags: tagsFrom(e.target.value) })} placeholder="tags, comma-separated" aria-label={`Part ${i + 1} tags`} /></div>
                  </div>
                </div>
              ))}
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={keepOriginal} onChange={e => setKeepOriginal(e.target.checked)} />
              Keep the source note (don't archive it)
            </label>
            <p className="text-[11px] text-muted">Bodies stay verbatim — you're editing titles and filing only.</p>
          </>
        )}
        <div className="flex justify-end gap-2">
          <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
          <Btn onClick={() => void save()} busy={busy} disabled={p.kind === 'new_task' && !task.title.trim()}>Approve with edits</Btn>
        </div>
      </div>
    </Modal>
  )
}

export default BrainReview
