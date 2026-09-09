/** Note detail: body (markdown, editable), dated entries (append log),
 * filing chips, and create-and-link graduation — New task / New reminder.
 * The note never converts; created items link back. */
import { useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { post, useNote, useRelatedNotes, when, type RelatedLinkItem, type RelatedNoteItem } from '../api'
import { GlassCard } from '../components/GlassCard'
import { Markdown } from '../components/chat/Markdown'
import { AttachToNote, DisputedChip, FileNoteModal, NewReminderFromNoteModal, NewTaskFromNoteModal, NoteAttachments, NoteRow, ProposalBanner, TYPE_TONE, PROVENANCE, PROVENANCE_HINT } from '../components/brain'
import { Btn, ConfirmModal, TextArea } from '../components/Modal'
import { Chip, Crumbs, Empty, Label, Loading, Skeleton } from '../components/ui'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'
import clsx from 'clsx'

function LinkedTargetRow({ item }: { item: RelatedLinkItem }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Chip tone="accent">{item.kind === 'task' ? 'task' : 'reminder'}</Chip>
      {item.kind === 'task'
        ? <Link to={`/tasks/${item.target_id}`} className="hover:text-accent-2">#{item.target_id} — {item.target?.title ?? '(deleted)'}</Link>
        : <Link to="/schedules" className="hover:text-accent-2">{item.target?.name ?? '(deleted)'} <span className="font-mono text-[10px] text-muted">{item.target?.cron}</span></Link>}
      {item.kind === 'task' && item.target?.status && <Chip>{item.target.status}</Chip>}
    </div>
  )
}

/** The note↔note branch of a Linked item — kept separate from `NoteRow` so a
 * conflicting pair still surfaces the DisputedChip the old Linked card had. */
function LinkedNoteRow({ item, from }: { item: RelatedNoteItem; from: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Chip tone="accent">{item.disputed ? 'conflicts with' : 'note'}</Chip>
      <Link to={`/brain/note/${item.id}`} state={{ from }} className="hover:text-accent-2">#{item.id} — {item.title}</Link>
      {/* append_entry archives its source — a linked note that's since been
          archived shouldn't read as still live. */}
      {item.status === 'archived' && <Chip>archived</Chip>}
      {!!item.disputed && <DisputedChip />}
    </div>
  )
}

/** The tag to send "more" to for the Shared-tags group: whichever tag
 * appears most often across the shown items' `shared_tags` — `n.tags[0]`
 * can be a tag none of the overflow notes actually matched on. */
function topSharedTag(items: RelatedNoteItem[]): string | null {
  const counts = new Map<string, number>()
  for (const item of items) for (const t of item.shared_tags ?? []) counts.set(t, (counts.get(t) ?? 0) + 1)
  let best: string | null = null; let bestCount = 0
  for (const [t, n] of counts) if (n > bestCount) { best = t; bestCount = n }
  return best
}

export function BrainNote() {
  const { id = '' } = useParams()
  const nid = Number(id)
  const q = useNote(nid)
  const n = q.data
  const relatedQ = useRelatedNotes(nid)
  const related = relatedQ.data
  usePageTitle(n?.title ?? `Note #${id}`)
  const qc = useQueryClient(); const toast = useToast(); const nav = useNavigate()
  const loc = useLocation()
  // The Link that led here can pass state.from (pathname + search) so
  // archiving returns the owner to the filtered list they came from,
  // instead of always bouncing to the unfiltered Library.
  const from = (loc.state as { from?: string } | null)?.from
  const [modal, setModal] = useState<'file' | 'task' | 'reminder' | 'archive' | null>(null)
  const [editing, setEditing] = useState(false)
  const [bodyDraft, setBodyDraft] = useState('')
  const [entry, setEntry] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const act = async (key: string, fn: () => Promise<unknown>, msg?: string) => {
    setBusy(key)
    try { await fn(); if (msg) toast(msg); qc.invalidateQueries() }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') }
    finally { setBusy(null) }
  }
  if (q.isLoading) return <section className="mx-auto max-w-4xl p-4 sm:p-6"><Loading rows={4} /></section>
  if (q.isError || !n) return <section className="mx-auto max-w-4xl p-6"><Empty error title={`Could not load note #${id}`} note={String(q.error ?? '404')} /></section>
  const areaLabel = n.area ? (n.area.parent ? `${n.area.parent} / ${n.area.name}` : n.area.name) : null
  // A retired area still names this note's home — muted and marked, never
  // dropped, or an archived note would read as though it was never filed.
  const areaGone = !!n.area?.archived
  return (
    <section className="mx-auto max-w-4xl p-4 sm:p-6">
      <Crumbs items={[['Second Brain', '/brain'], ['Library', '/brain/library'], [`#${n.id}`]]} />
      <ProposalBanner noteId={nid} />
      {modal === 'file' && <FileNoteModal n={n} onClose={() => setModal(null)} />}
      {modal === 'task' && <NewTaskFromNoteModal n={n} onClose={() => setModal(null)} />}
      {modal === 'reminder' && <NewReminderFromNoteModal n={n} onClose={() => setModal(null)} />}
      {modal === 'archive' && (
        <ConfirmModal title="Archive note" confirmLabel="Archive"
          message={`Archive “${n.title}”? It leaves the library views but stays searchable — nothing is deleted.`}
          onClose={() => setModal(null)}
          onConfirm={async () => { await act('archive', () => post(`/api/note/${n.id}/edit`, { status: 'archived' }), 'Archived'); setModal(null); nav(from ?? '/brain/library', { replace: true }) }} />
      )}

      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <h1 className="min-w-0 break-words text-xl font-semibold tracking-tight">{n.title}</h1>
        <div className="flex flex-wrap gap-2">
          <Btn onClick={() => setModal('task')}>+ Task</Btn>
          <Btn kind="ghost" onClick={() => setModal('reminder')}>+ Reminder</Btn>
          <Btn kind="ghost" onClick={() => setModal('file')}>{n.status === 'inbox' ? 'File…' : 'Refile…'}</Btn>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {n.status === 'inbox' && <Chip tone="accent">inbox</Chip>}
        {n.status === 'archived' && <Chip>archived</Chip>}
        {!!n.disputed && (
          <span className="inline-flex items-center gap-1.5">
            <DisputedChip />
            <button type="button" disabled={busy === 'disputed'}
              className="font-mono text-[10px] uppercase tracking-wider text-muted underline hover:text-fg"
              onClick={() => void act('disputed', () => post(`/api/note/${n.id}/edit`, { disputed: false }), 'Dispute cleared on this note')}>
              resolve
            </button>
          </span>
        )}
        <span className={clsx('font-mono text-[10px] uppercase tracking-wider', TYPE_TONE[n.type])}>{n.type}</span>
        <button type="button" onClick={() => setModal('file')} title="Edit area, project, tags" className="flex flex-wrap items-center gap-1.5 hover:opacity-80">
          {areaLabel
            ? (areaGone
                ? <Chip><span title="This area was retired — the note keeps its filing">{areaLabel} · retired</span></Chip>
                : <Chip tone="accent">{areaLabel}</Chip>)
            : <Chip>no area — set ✎</Chip>}
          {n.tags.map(t => <Chip key={t}>{t}</Chip>)}
        </button>
        {n.project && <Link to={`/projects/${n.project.slug}`}><Chip>{n.project.name}</Chip></Link>}
        <span className="ml-auto font-mono text-[10px] text-muted">{n.authored_by !== 'owner' && <span title={PROVENANCE_HINT[n.authored_by] ?? ''}>{PROVENANCE[n.authored_by] ?? n.authored_by} · </span>}updated {when(n.updated_at ?? n.created_at)}</span>
      </div>

      <GlassCard className="mb-4">
        {editing ? (
          <div className="flex flex-col gap-2">
            <TextArea rows={12} value={bodyDraft} onChange={e => setBodyDraft(e.target.value)} />
            <div className="flex justify-end gap-2">
              <Btn kind="ghost" onClick={() => setEditing(false)}>Cancel</Btn>
              <Btn busy={busy === 'body'} onClick={() => void act('body', async () => { await post(`/api/note/${n.id}/edit`, { body: bodyDraft }); setEditing(false) }, 'Saved')}>Save</Btn>
            </div>
          </div>
        ) : (
          <>
            {n.body ? <div className="text-sm"><Markdown text={n.body} /></div> : <p className="text-sm text-muted">No body yet — Edit to add one.</p>}
            <div className="mt-3 flex flex-wrap gap-2 border-t border-line-subtle pt-3">
              <Btn kind="ghost" onClick={() => { setBodyDraft(n.body); setEditing(true) }}>Edit</Btn>
              <Btn kind="ghost" busy={busy === 'pin'} onClick={() => void act('pin', () => post(`/api/note/${n.id}/edit`, { pinned: !n.pinned }), n.pinned ? 'Unpinned' : 'Pinned')}>{n.pinned ? 'Unpin' : 'Pin'}</Btn>
              {n.status !== 'archived'
                ? <Btn kind="warn" onClick={() => setModal('archive')}>Archive</Btn>
                : (() => {
                    // A homeless note can't restore to active (store rule) —
                    // land it back in the inbox instead of round-tripping a 409.
                    const homeless = !n.area_id && !n.project_id
                    const status = homeless ? 'inbox' : 'active'
                    return <Btn kind="ghost" busy={busy === 'restore'} onClick={() => void act('restore', () => post(`/api/note/${n.id}/edit`, { status }), 'Restored')}>{homeless ? 'Restore to inbox' : 'Restore'}</Btn>
                  })()}
            </div>
          </>
        )}
      </GlassCard>

      <GlassCard className="mb-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <Label>Attachments{n.attachments?.length ? ` · ${n.attachments.length}` : ''}</Label>
          <AttachToNote noteId={n.id} onChange={() => void q.refetch()} />
        </div>
        {n.attachments?.length
          ? <NoteAttachments items={n.attachments} onChange={() => void q.refetch()} />
          : <p className="text-xs text-muted">No photos or voice memos yet.</p>}
      </GlassCard>

      <GlassCard className="mb-4">
        <div className="flex items-center justify-between"><Label>Thoughts · {n.entries.length}</Label></div>
        <form className="mt-2 flex gap-2" onSubmit={e => { e.preventDefault(); if (!entry.trim()) return; void act('entry', async () => { await post(`/api/note/${n.id}/entries`, { body: entry.trim() }); setEntry('') }, 'Thought added') }}>
          <TextArea rows={2} value={entry} onChange={e => setEntry(e.target.value)} placeholder="Add a dated thought — meeting outcome, follow-up, idea…" />
          <Btn busy={busy === 'entry'} disabled={!entry.trim()} className="self-end">Add</Btn>
        </form>
        <div className="mt-3 flex flex-col gap-3">
          {n.entries.map(e => (
            <div key={e.id} className="border-t border-line-subtle pt-2 first:border-0 first:pt-0">
              <p className="font-mono text-[10px] text-accent-2">{when(e.created_at)}</p>
              <div className="mt-0.5 text-sm"><Markdown text={e.body} /></div>
            </div>
          ))}
          {n.entries.length === 0 && <p className="text-xs text-muted">No thoughts yet — use them as an append log (1:1s, running topics).</p>}
        </div>
      </GlassCard>

      {/* A multi-part series reads as a document, so its navigation sits ABOVE
          the Related card and looks nothing like it: where you are, and the two
          steps either side. Recovered from the numbered titles the dumps kept
          (`1 - Initialize` … `9 - Authentication`) plus the folder tag — no new
          schema, see wm_store._series_for. */}
      {related?.series && (
        <GlassCard>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label>Part {related.series.position} of {related.series.total}</Label>
            <Link to={`/brain/library?tag=${encodeURIComponent(related.series.tag)}`}
                  className="font-mono text-[10px] uppercase tracking-wider text-accent-2 hover:underline">
              {related.series.tag}
            </Link>
          </div>
          <div className="mt-2 flex flex-wrap items-stretch gap-2">
            {related.series.prev
              ? <Link to={`/brain/note/${related.series.prev.id}`} className="glass min-w-0 flex-1 rounded-xl px-3 py-2 hover:bg-raised">
                  <span className="font-mono text-[10px] uppercase tracking-wider text-muted">← previous</span>
                  <p className="truncate text-sm">{related.series.prev.title}</p>
                </Link>
              : <div className="min-w-0 flex-1 rounded-xl border border-dashed border-line-subtle px-3 py-2">
                  <span className="font-mono text-[10px] uppercase tracking-wider text-muted">start of series</span>
                </div>}
            {related.series.next
              ? <Link to={`/brain/note/${related.series.next.id}`} className="glass min-w-0 flex-1 rounded-xl px-3 py-2 text-right hover:bg-raised">
                  <span className="font-mono text-[10px] uppercase tracking-wider text-muted">next →</span>
                  <p className="truncate text-sm">{related.series.next.title}</p>
                </Link>
              : <div className="min-w-0 flex-1 rounded-xl border border-dashed border-line-subtle px-3 py-2 text-right">
                  <span className="font-mono text-[10px] uppercase tracking-wider text-muted">end of series</span>
                </div>}
          </div>
        </GlassCard>
      )}

      {related && (related.linked.items.length > 0 || related.shared_tags.items.length > 0 || related.same_area.items.length > 0) && (
        <GlassCard>
          <Label>Related</Label>
          <div className="mt-2 flex flex-col gap-4">
            {related.linked.items.length > 0 && (
              <div>
                <p className="mb-2 font-mono text-[10px] uppercase tracking-wider text-muted">Linked</p>
                <div className="flex flex-col gap-2 text-sm">
                  {related.linked.items.map(item => item.kind === 'note'
                    ? <LinkedNoteRow key={`note-${item.id}`} item={item} from={loc.pathname + loc.search} />
                    : <LinkedTargetRow key={`${item.kind}-${item.target_id}`} item={item} />)}
                  {/* No Library view filters by "linked to note X" — say the
                      count in plain text instead of a dead-end "more" link. */}
                  {related.linked.more > 0 && <p className="text-[11px] text-muted">+{related.linked.more} more linked</p>}
                </div>
              </div>
            )}
            {related.shared_tags.items.length > 0 && (
              <div>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="font-mono text-[10px] uppercase tracking-wider text-muted">Shared tags</p>
                  {related.shared_tags.more > 0 && (() => {
                    const tag = topSharedTag(related.shared_tags.items) ?? n.tags[0]
                    return tag ? (
                      <Link to={`/brain/library?tag=${encodeURIComponent(tag)}`} className="text-[11px] text-accent-2 hover:underline">more ({related.shared_tags.more}) · {tag}</Link>
                    ) : null
                  })()}
                </div>
                <div className="flex flex-col gap-2">
                  {related.shared_tags.items.map(item => (
                    <div key={`shared-${item.id}`} className="flex flex-col gap-1">
                      <NoteRow n={item} showBody={false} />
                      {!!item.shared_tags?.length && <p className="pl-1 text-[11px] text-muted">via {item.shared_tags.join(', ')}</p>}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {related.same_area.items.length > 0 && (
              <div>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="font-mono text-[10px] uppercase tracking-wider text-muted">Same area</p>
                  {related.same_area.more > 0 && !!n.area_id && (
                    <Link to={`/brain/library?area=${n.area_id}`} className="text-[11px] text-accent-2 hover:underline">more ({related.same_area.more})</Link>
                  )}
                </div>
                <div className="flex flex-col gap-2">
                  {related.same_area.items.map(item => <NoteRow key={`area-${item.id}`} n={item} showBody={false} />)}
                </div>
              </div>
            )}
          </div>
        </GlassCard>
      )}
      {relatedQ.isLoading && (
        <GlassCard>
          <Label>Related</Label>
          <div className="mt-2"><Skeleton rows={3} /></div>
        </GlassCard>
      )}
      {relatedQ.isError && !related && (
        <GlassCard>
          <Label>Related</Label>
          <p className="mt-2 text-xs text-error">Could not load related notes.</p>
        </GlassCard>
      )}
    </section>
  )
}
