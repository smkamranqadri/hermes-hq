/** Note detail: body (markdown, editable), dated entries (append log),
 * filing chips, and create-and-link graduation — New task / New reminder.
 * The note never converts; created items link back. */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { post, useNote, when } from '../api'
import { GlassCard } from '../components/GlassCard'
import { Markdown } from '../components/chat/Markdown'
import { FileNoteModal, NewReminderFromNoteModal, NewTaskFromNoteModal, TYPE_TONE } from '../components/brain'
import { Btn, ConfirmModal, TextArea } from '../components/Modal'
import { Chip, Crumbs, Empty, Label, Loading } from '../components/ui'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'
import clsx from 'clsx'

export function BrainNote() {
  const { id = '' } = useParams()
  const nid = Number(id)
  const q = useNote(nid)
  const n = q.data
  usePageTitle(n?.title ?? `Note #${id}`)
  const qc = useQueryClient(); const toast = useToast(); const nav = useNavigate()
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
  return (
    <section className="mx-auto max-w-4xl p-4 sm:p-6">
      <Crumbs items={[['Second Brain', '/brain'], ['Library', '/brain/library'], [`#${n.id}`]]} />
      {modal === 'file' && <FileNoteModal n={n} onClose={() => setModal(null)} />}
      {modal === 'task' && <NewTaskFromNoteModal n={n} onClose={() => setModal(null)} />}
      {modal === 'reminder' && <NewReminderFromNoteModal n={n} onClose={() => setModal(null)} />}
      {modal === 'archive' && (
        <ConfirmModal title="Archive note" confirmLabel="Archive"
          message={`Archive “${n.title}”? It leaves the library views but stays searchable — nothing is deleted.`}
          onClose={() => setModal(null)}
          onConfirm={async () => { await act('archive', () => post(`/api/note/${n.id}/edit`, { status: 'archived' }), 'Archived'); setModal(null); nav('/brain/library') }} />
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
        <span className={clsx('font-mono text-[10px] uppercase tracking-wider', TYPE_TONE[n.type])}>{n.type}</span>
        <button type="button" onClick={() => setModal('file')} title="Edit area, project, tags" className="flex flex-wrap items-center gap-1.5 hover:opacity-80">
          {areaLabel ? <Chip tone="accent">{areaLabel}</Chip> : <Chip>no area — set ✎</Chip>}
          {n.tags.map(t => <Chip key={t}>{t}</Chip>)}
        </button>
        {n.project && <Link to={`/projects/${n.project.slug}`}><Chip>{n.project.name}</Chip></Link>}
        <Chip>{n.authored_by}</Chip>
        <span className="ml-auto font-mono text-[10px] text-muted">updated {when(n.updated_at ?? n.created_at)}</span>
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
                : <Btn kind="ghost" busy={busy === 'restore'} onClick={() => void act('restore', () => post(`/api/note/${n.id}/edit`, { status: 'active' }), 'Restored')}>Restore</Btn>}
            </div>
          </>
        )}
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

      {n.links.length > 0 && (
        <GlassCard>
          <Label>Linked</Label>
          <div className="mt-2 flex flex-col gap-2 text-sm">
            {n.links.map(l => (
              <div key={`${l.kind}-${l.target_id}`} className="flex flex-wrap items-center gap-2">
                <Chip tone="accent">{l.kind === 'task' ? 'task' : 'reminder'}</Chip>
                {l.kind === 'task'
                  ? <Link to={`/tasks/${l.target_id}`} className="hover:text-accent-2">#{l.target_id} — {l.target?.title ?? '(deleted)'}</Link>
                  : <Link to="/schedules" className="hover:text-accent-2">{l.target?.name ?? '(deleted)'} <span className="font-mono text-[10px] text-muted">{l.target?.cron}</span></Link>}
                {l.kind === 'task' && l.target?.status && <Chip>{l.target.status}</Chip>}
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </section>
  )
}
