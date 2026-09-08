/** Second Brain shared pieces: sub-nav, note rows, file/edit + graduation
 * modals (intent/SecondBrainPlan.md P1). Notes never convert — "New task" /
 * "New reminder" create linked items and the note stays a note. */
import { useEffect, useRef, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { ago, attachmentUrl, del, get, post, postForm, useAreas, useNotesTree, useProjects, useRoster, type Area, type Note, type NoteAttachment, type NoteFull, type Proposal, type SplitPart } from '../api'
import { Btn, Field, Modal, SelectInput, TextArea, TextInput } from './Modal'
import { Chip, Label } from './ui'
import { useToast } from './Toast'

/** Heading-row pill nav: Home · Library · Review. */
export function BrainSubNav({ inbox, review }: { inbox?: number; review?: number }) {
  const pill = ({ isActive }: { isActive: boolean }) => clsx('rounded-full px-3 py-1 text-[13px] whitespace-nowrap', isActive ? 'bg-fg font-semibold text-bg' : 'text-muted hover:text-fg')
  return (
    <nav className="flex w-fit items-center gap-1 rounded-full border border-line bg-glass p-1">
      <NavLink to="/brain" end className={pill}>Home{typeof inbox === 'number' && inbox > 0 ? ` · ${inbox}` : ''}</NavLink>
      <NavLink to="/brain/library" className={pill}>Library</NavLink>
      <NavLink to="/brain/review" className={pill}>Review{typeof review === 'number' && review > 0 ? ` · ${review}` : ''}</NavLink>
    </nav>
  )
}

export const TYPE_TONE: Record<Note['type'], string> = { note: 'text-muted', playbook: 'text-accent-2', wiki: 'text-queued' }

/** An inbox note the librarian has already triaged — the decision lives in
 * the Review queue, so lists say so instead of inviting a double-filing.
 * `link=false` renders a plain span for use inside row-links (no nested <a>). */
/** The proposal pill style, one definition per tone. Written out in full —
 * Tailwind scans source statically, so an interpolated color class would
 * never be generated. */
const PILL = 'inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider'
export const pillCls = (tone: 'working' | 'needsyou') =>
  `${PILL} ${tone === 'working' ? 'border-working/60 text-working' : 'border-needsyou/60 text-needsyou'}`

export function PendingProposalChip({ link = true }: { link?: boolean }) {
  const cls = pillCls('working')
  if (!link) return <span className={cls}>librarian proposed</span>
  return <Link to="/brain/review" className={`${cls} hover:bg-working/10`}>librarian proposed → review</Link>
}

/** A note flagged by an approved contradiction proposal — keep-both until the
 * owner resolves it (clear the flag from the note page). */
export function DisputedChip() {
  return (
    <span title="Contradiction — both notes kept until you resolve it" className={pillCls('needsyou')}>
      disputed
    </span>
  )
}

/** File-to-Archive proposals must read differently from real filings — the
 * owner is approving "this is junk/museum", not "this belongs somewhere". */
function ArchiveChip() {
  return <span className={pillCls('needsyou')}>→ archive</span>
}

export const KIND_LABEL: Record<Proposal['kind'], string> = { split: 'split', file: 'file', contradiction: 'contradiction', new_task: 'new task' }

/** Record-keyed so adding a proposal kind fails compilation here instead of
 * silently inheriting another kind's button copy. */
export function approveLabel(p: Proposal): string {
  const labels: Record<Proposal['kind'], string> = {
    split: `Approve split (${p.payload?.parts?.length ?? 0} notes)`,
    file: p.payload?.archive ? 'Approve → archive' : 'Approve filing',
    contradiction: 'Mark both disputed',
    new_task: 'Create task & link',
  }
  return labels[p.kind]
}

/** Kind-specific payload rendering, shared by the Review queue and the
 * note-page banner so every surface tells the same story. */
export function ProposalPayloadView({ p, compact = false }: { p: Proposal; compact?: boolean }) {
  const areas = useAreas(); const projects = useProjects()
  const chips = (f: { area_id?: number; project_id?: number; tags?: string[]; type?: string; archive?: boolean }, inboxFallback = false) => {
    const area = areaLabel(areas.data?.areas, f.area_id ?? null)
    const proj = f.project_id ? (projects.data?.projects ?? []).find(x => x.id === f.project_id) : null
    return (
      <>
        {f.archive && <ArchiveChip />}
        {area && <Chip tone="accent">{area}</Chip>}
        {proj && <Chip tone="accent">{proj.name}</Chip>}
        {f.type && f.type !== 'note' && <Chip>{f.type}</Chip>}
        {(f.tags ?? []).map(t => <Chip key={t}>{t}</Chip>)}
        {inboxFallback && !f.archive && !area && !proj && <Chip>→ inbox</Chip>}
      </>
    )
  }
  // Declared tag coinage renders loudly — approving registers these in the
  // closed taxonomy, so the owner should notice before clicking.
  const newTags = (p.payload?.new_tags ?? []).map(t => (
    <span key={t} className={pillCls('working')}>new tag: {t}</span>
  ))
  // A filed note always leaves the inbox, so file gets NO inbox-fallback
  // chip; an unfiled split part genuinely lands back in the inbox, so split
  // parts DO — that chip is the owner's pre-approval warning.
  if (p.kind === 'file' && p.payload) return <div className="mt-2 flex flex-wrap items-center gap-1.5">{chips(p.payload)}{newTags}</div>
  if (p.kind === 'split') return (
    <ol className="mt-2 flex flex-col gap-1.5">
      {(p.payload?.parts ?? []).map((part: SplitPart, i: number) => (
        <li key={i} className={clsx('rounded-lg border border-line-subtle bg-inset px-3', compact ? 'py-1.5' : 'py-2')}>
          <div className={clsx('flex flex-wrap items-center gap-1.5', compact && 'text-xs')}>
            <span className={clsx('font-medium', compact ? '' : 'text-xs')}>{part.title}</span>
            {chips(part, true)}
          </div>
          {!compact && part.body && <p className="mt-1 line-clamp-2 text-[11px] text-muted">{part.body}</p>}
        </li>
      ))}
      {newTags.length > 0 && <li className="flex flex-wrap gap-1.5">{newTags}</li>}
      {!compact && p.payload?.archive_original === false && <p className="text-[11px] text-muted">Source note stays (not archived).</p>}
    </ol>
  )
  if (p.kind === 'contradiction') return (
    <div className="mt-2 rounded-lg border border-needsyou/40 bg-inset px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <DisputedChip />
        <span className="text-muted">conflicts with</span>
        <Link to={`/brain/note/${p.payload?.other_note_id}`} className="font-medium hover:text-accent-2">note #{p.payload?.other_note_id}</Link>
      </div>
      {p.payload?.explanation && <p className="mt-1 text-muted">{p.payload.explanation}</p>}
      <p className="mt-1 text-[11px] text-muted">Keep-both: approving flags both notes, nothing is merged or rewritten.</p>
    </div>
  )
  if (p.kind === 'new_task') return (
    <div className="mt-2 rounded-lg border border-line-subtle bg-inset px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip tone="accent">task</Chip>
        <span className="font-medium">{p.payload?.title}</span>
        {p.payload?.project_id != null && chips({ project_id: p.payload.project_id })}
        <Chip>{p.payload?.assignee ?? 'owner'}</Chip>
      </div>
      {p.payload?.description && <p className="mt-1 line-clamp-2 text-muted">{p.payload.description}</p>}
      <p className="mt-1 text-[11px] text-muted">Creates a real linked task — the note stays a note.</p>
    </div>
  )
  return null
}

export function areaLabel(areas: Area[] | undefined, id: number | null) {
  if (!id || !areas) return null
  const a = areas.find(x => x.id === id)
  if (!a) return null
  const parent = a.parent_id ? areas.find(x => x.id === a.parent_id) : null
  return parent ? `${parent.name} / ${a.name}` : a.name
}

/** One note in a list — Library results, project tab, Home recents. */
export function NoteRow({ n, areas, showBody = true }: { n: Note; areas?: Area[]; showBody?: boolean }) {
  const area = areaLabel(areas, n.area_id)
  return (
    <Link to={`/brain/note/${n.id}`} className="glass block rounded-xl px-4 py-3 hover:bg-raised">
      <div className="flex flex-wrap items-center gap-2">
        {n.type !== 'note' && <span className={clsx('font-mono text-[10px] uppercase tracking-wider', TYPE_TONE[n.type])}>{n.type}</span>}
        {!!n.pinned && <span className="font-mono text-[10px] text-needsyou" title="Pinned">★</span>}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{n.title}</span>
        {(n.entry_count ?? 0) > 0 && <span className="font-mono text-[10px] text-muted">{n.entry_count} thought{n.entry_count === 1 ? '' : 's'}</span>}
        <span className="font-mono text-[10px] text-muted">{ago(n.updated_at ?? n.created_at)}</span>
      </div>
      {showBody && n.body && <p className="mt-1 line-clamp-2 text-xs text-muted">{n.body}</p>}
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {area && <Chip tone="accent">{area}</Chip>}
        {n.tags.slice(0, 4).map(t => <Chip key={t}>{t}</Chip>)}
        {n.status === 'archived' && <Chip>archived</Chip>}
        {!!n.attachment_counts?.photo && <Chip>🖼 {n.attachment_counts.photo}</Chip>}
        {!!n.attachment_counts?.voice && <Chip>🎤 {n.attachment_counts.voice}</Chip>}
        {/* 2c: a memo with no text is waiting on the OWNER, not the librarian.
            Standing rule — anything awaiting the owner shows on the row too. */}
        {n.awaiting_transcript && <span className={pillCls('needsyou')} title="Voice memo with no text yet — the librarian skips it until you write what it said">needs your words</span>}
        {!!n.disputed && <DisputedChip />}
        {!!n.pending_proposal_id && <PendingProposalChip link={false} />}
      </div>
    </Link>
  )
}

export const tagsFrom = (s: string) => s.split(',').map(t => t.trim()).filter(Boolean)

/** Two-level area picker — the ONE place the area optgroup tree is rendered
 * (FileNoteModal and the proposal editor both consume it). */
export function AreaSelect({ value, onChange, areas }: { value: string; onChange: (v: string) => void; areas: Area[] }) {
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

/** Project picker by id — shared for the same reason. */
export function ProjectSelect({ value, onChange, projects }: { value: string; onChange: (v: string) => void; projects: { id: number; name: string }[] }) {
  return (
    <SelectInput value={value} onChange={e => onChange(e.target.value)} aria-label="Project">
      <option value="">— no project —</option>
      {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
    </SelectInput>
  )
}

/** File / edit metadata: area, project, tags, type — the manual filing path
 * until the librarian proposes these in Phase 2. Filing an inbox note also
 * moves it to active. */
export function FileNoteModal({ n, onClose }: { n: Note; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const areas = useAreas(); const projects = useProjects()
  const [f, setF] = useState({
    area_id: n.area_id ? String(n.area_id) : '', project_id: n.project_id ? String(n.project_id) : '',
    type: n.type as string, tags: n.tags.join(', '),
  })
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await post(`/api/note/${n.id}/edit`, {
        area_id: f.area_id ? Number(f.area_id) : undefined, clear_area: !f.area_id,
        project_id: f.project_id ? Number(f.project_id) : undefined, clear_project: !f.project_id,
        type: f.type, tags: tagsFrom(f.tags),
        status: n.status === 'inbox' ? 'active' : undefined,
      })
      toast(n.status === 'inbox' ? 'Filed' : 'Saved'); qc.invalidateQueries(); onClose()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title={n.status === 'inbox' ? `File “${n.title}”` : 'Edit filing'} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Field label="Area">
          <AreaSelect value={f.area_id} onChange={v => setF(x => ({ ...x, area_id: v }))} areas={areas.data?.areas ?? []} />
        </Field>
        <Field label="Project" hint="Project-linked notes show on that project's page.">
          <ProjectSelect value={f.project_id} onChange={v => setF(x => ({ ...x, project_id: v }))} projects={projects.data?.projects ?? []} />
        </Field>
        <Field label="Type">
          <SelectInput value={f.type} onChange={e => setF(x => ({ ...x, type: e.target.value }))}>
            {['note', 'playbook', 'wiki'].map(t => <option key={t}>{t}</option>)}
          </SelectInput>
        </Field>
        <Field label="Tags" hint="Comma-separated."><TextInput value={f.tags} onChange={e => setF(x => ({ ...x, tags: e.target.value }))} placeholder="payments, 1:1" /></Field>
        <div className="flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn onClick={() => void save()} busy={busy}>{n.status === 'inbox' ? 'File note' : 'Save'}</Btn></div>
      </div>
    </Modal>
  )
}

/** Create-and-link a NEW task from a note (defaults to the owner's own todo —
 * the dispatcher never claims owner tasks). */
export function NewTaskFromNoteModal({ n, onClose }: { n: NoteFull; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const projects = useProjects(); const roster = useRoster()
  const [f, setF] = useState({ title: n.title, project: n.project?.slug ?? '', assignee: 'owner' })
  const [busy, setBusy] = useState(false)
  const save = async () => {
    if (!f.project) { toast('Pick a project — tasks live in projects', 'err'); return }
    setBusy(true)
    try {
      const r = await post<{ id: number }>(`/api/note/${n.id}/new-task`, { title: f.title, project: f.project, assignee: f.assignee })
      toast(`Task #${r.id} created & linked`); qc.invalidateQueries(); onClose()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title="New task from this note" onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Field label="Title"><TextInput value={f.title} onChange={e => setF(x => ({ ...x, title: e.target.value }))} /></Field>
        <Field label="Project">
          <SelectInput value={f.project} onChange={e => setF(x => ({ ...x, project: e.target.value }))}>
            <option value="">— pick —</option>
            {(projects.data?.projects ?? []).map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}
          </SelectInput>
        </Field>
        <Field label="Assignee" hint="owner = your own todo; the dispatcher skips it. Pick an agent to hand it off.">
          <SelectInput value={f.assignee} onChange={e => setF(x => ({ ...x, assignee: e.target.value }))}>
            {(roster.data?.assignees ?? ['owner']).map(a => <option key={a}>{a}</option>)}
          </SelectInput>
        </Field>
        <p className="text-[11px] text-muted">The note stays a note — the task links back to it.</p>
        <div className="flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn onClick={() => void save()} busy={busy}>Create & link</Btn></div>
      </div>
    </Modal>
  )
}

/** Create-and-link a NEW reminder: a schedule that mints an owner task on its
 * cron — your word for "routine". */
export function NewReminderFromNoteModal({ n, onClose }: { n: NoteFull; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const projects = useProjects()
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10)
  const [f, setF] = useState({ name: n.title, project: n.project?.slug ?? '', cron: '0 9 * * *' })
  const [preset, setPreset] = useState({ kind: 'once', at: '09:00', dow: 'mon', day: 1, every_hours: 6, date: tomorrow })
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (preset.kind === 'custom') return
    if (preset.kind === 'once') {
      // one date + time -> a plain cron for that calendar moment; one_shot retires it after firing
      const [hh, mm] = preset.at.split(':')
      const d = new Date(preset.date + 'T00:00:00')
      if (!Number.isNaN(d.getTime())) setF(x => ({ ...x, cron: `${Number(mm)} ${Number(hh)} ${d.getDate()} ${d.getMonth() + 1} *` }))
      return
    }
    post<{ cron: string }>('/api/schedules/compile', preset).then(r => setF(x => ({ ...x, cron: r.cron }))).catch(() => undefined)
  }, [preset])
  const save = async () => {
    if (!f.project) { toast('Pick a project — reminders mint tasks there', 'err'); return }
    setBusy(true)
    try {
      const r = await post<{ id: number }>(`/api/note/${n.id}/new-reminder`, { name: f.name, project: f.project, cron: f.cron, one_shot: preset.kind === 'once' })
      toast(`Reminder #${r.id} created & linked`); qc.invalidateQueries(); onClose()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title="New reminder from this note" onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Field label="Name"><TextInput value={f.name} onChange={e => setF(x => ({ ...x, name: e.target.value }))} /></Field>
        <Field label="Project">
          <SelectInput value={f.project} onChange={e => setF(x => ({ ...x, project: e.target.value }))}>
            <option value="">— pick —</option>
            {(projects.data?.projects ?? []).map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}
          </SelectInput>
        </Field>
        <Field label="Repeat">
          <SelectInput value={preset.kind} onChange={e => setPreset(x => ({ ...x, kind: e.target.value }))}>
            {['once', 'daily', 'weekdays', 'weekly', 'monthly', 'custom'].map(k => <option key={k}>{k}</option>)}
          </SelectInput>
        </Field>
        <Field label={preset.kind === 'custom' ? 'Cron' : preset.kind === 'once' ? 'On (PKT)' : 'At (PKT)'}>
          {preset.kind === 'custom'
            ? <TextInput value={f.cron} onChange={e => setF(x => ({ ...x, cron: e.target.value }))} placeholder="0 9 * * *" />
            : <div className="flex flex-wrap gap-2">
                {preset.kind === 'once' && <div className="min-w-0 flex-1 basis-36"><TextInput type="date" value={preset.date} onChange={e => setPreset(x => ({ ...x, date: e.target.value }))} aria-label="Date" /></div>}
                <div className="min-w-0 flex-1 basis-28"><TextInput type="time" value={preset.at} onChange={e => setPreset(x => ({ ...x, at: e.target.value }))} aria-label="Time" /></div>
                {preset.kind === 'weekly' && <div className="min-w-0 flex-1 basis-28"><SelectInput value={preset.dow} onChange={e => setPreset(x => ({ ...x, dow: e.target.value }))}>{['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(d => <option key={d}>{d}</option>)}</SelectInput></div>}
                {preset.kind === 'monthly' && <div className="w-24"><TextInput type="number" min={1} max={28} value={preset.day} onChange={e => setPreset(x => ({ ...x, day: Number(e.target.value) }))} aria-label="Day of month" /></div>}
              </div>}
        </Field>
        <p className="text-[11px] text-muted">{preset.kind === 'once' ? 'Fires once as a task assigned to you, then the reminder retires itself.' : 'Fires as a task assigned to you (never dispatched to agents).'} · cron <span className="font-mono">{f.cron}</span></p>
        <div className="flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn onClick={() => void save()} busy={busy}>Create & link</Btn></div>
      </div>
    </Modal>
  )
}

/** Capture editor: multi-line by design — batch dumps are the normal case.
 * First line becomes the title; the librarian will split batches in P2. */
/** 2c: staged photo/voice, held in memory until the note exists.
 *  Capture creates the note first, then uploads — so a file picked before the
 *  note is written has to wait somewhere. */
type Pending = { file: Blob; name: string; kind: 'photo' | 'voice'; mime: string; durationMs?: number }

const MEDIA_MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
/** iOS Safari supports audio/mp4 only; Chrome prefers webm/opus. Pick what
 *  THIS browser admits to, and let the server store whatever comes out. */
function pickAudioMime(): string | undefined {
  const R = (window as unknown as { MediaRecorder?: typeof MediaRecorder }).MediaRecorder
  if (!R?.isTypeSupported) return undefined
  return MEDIA_MIME_CANDIDATES.find(m => R.isTypeSupported(m))
}

export async function uploadAttachment(noteId: number, p: Pending) {
  const fd = new FormData()
  fd.append('file', p.file, p.name)
  fd.append('kind', p.kind)
  if (p.durationMs) fd.append('duration_ms', String(Math.round(p.durationMs)))
  return postForm(`/api/note/${noteId}/attachments`, fd)
}

/** Record / stop, with the elapsed seconds visible so a memo is never a
 *  mystery blob. Renders nothing where MediaRecorder does not exist. */
export function VoiceRecorder({ onDone, disabled }: { onDone: (p: Pending) => void; disabled?: boolean }) {
  const toast = useToast()
  const [rec, setRec] = useState<MediaRecorder | null>(null)
  const [secs, setSecs] = useState(0)
  const startedAt = useRef(0)
  const supported = typeof window !== 'undefined' &&
    !!(window as unknown as { MediaRecorder?: unknown }).MediaRecorder &&
    !!navigator.mediaDevices?.getUserMedia
  useEffect(() => {
    if (!rec) return
    const t = setInterval(() => setSecs(Math.floor((Date.now() - startedAt.current) / 1000)), 250)
    return () => clearInterval(t)
  }, [rec])
  if (!supported) return null

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType = pickAudioMime()
      const r = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      const chunks: BlobPart[] = []
      r.ondataavailable = e => { if (e.data.size) chunks.push(e.data) }
      r.onstop = () => {
        stream.getTracks().forEach(t => t.stop())          // release the mic light
        const mime = r.mimeType || mimeType || 'audio/webm'
        const blob = new Blob(chunks, { type: mime })
        const ext = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm'
        setRec(null); setSecs(0)
        if (blob.size) onDone({ file: blob, name: `memo.${ext}`, kind: 'voice', mime, durationMs: Date.now() - startedAt.current })
      }
      startedAt.current = Date.now()
      r.start()
      setRec(r)
    } catch (e) {
      // The usual causes are a denied permission and an insecure context —
      // getUserMedia does not exist over plain http, only over the ts.net URL.
      const insecure = !window.isSecureContext
      toast(insecure ? 'Recording needs the https address — open the ts.net link, not http://'
                     : (e instanceof Error ? e.message : String(e)), 'err')
    }
  }
  return rec
    ? <Btn kind="warn" type="button" onClick={() => rec.stop()}>
        ■ Stop {String(Math.floor(secs / 60)).padStart(2, '0')}:{String(secs % 60).padStart(2, '0')}
      </Btn>
    : <Btn kind="ghost" type="button" disabled={disabled} onClick={() => void start()}>● Record</Btn>
}

/** The staged-attachment strip: what will be attached once you hit Capture. */
function PendingStrip({ items, onRemove }: { items: Pending[]; onRemove: (i: number) => void }) {
  if (!items.length) return null
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((p, i) => (
        <span key={i} className="inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[11px]">
          {p.kind === 'photo' ? '🖼' : '🎤'} {p.name}
          {p.durationMs ? ` · ${Math.round(p.durationMs / 1000)}s` : ''}
          <button type="button" onClick={() => onRemove(i)} className="text-muted hover:text-fg" aria-label={`Remove ${p.name}`}>×</button>
        </span>
      ))}
    </div>
  )
}

export function CaptureBox({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient(); const toast = useToast()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<Pending[]>([])
  const photoRef = useRef<HTMLInputElement>(null)
  const capture = async () => {
    const t = text.trim()
    // 2c: a voice memo or photo alone is a real capture — requiring text would
    // defeat the point of recording something you have no time to type.
    if (!t && !pending.length) return
    setBusy(true)
    try {
      // the FULL capture is the body; the first line only NAMES the note.
      // With no text at all, name it after what it IS so the inbox is legible.
      const auto = pending[0]?.kind === 'voice' ? 'Voice memo' : 'Photo'
      const title = t ? t.split('\n')[0].slice(0, 120)
        : `${auto} · ${new Date().toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}`
      const { id } = await post<{ id: number }>('/api/notes', { title, body: t })
      for (const p of pending) await uploadAttachment(id, p)
      setText(''); setPending([])
      toast(pending.length ? `Captured to inbox with ${pending.length} attachment${pending.length === 1 ? '' : 's'}` : 'Captured to inbox')
      qc.invalidateQueries()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  const onPhoto = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    setPending(p => [...p, ...files.map(f => ({ file: f, name: f.name, kind: 'photo' as const, mime: f.type }))])
    e.target.value = ''            // so picking the same photo twice still fires
  }
  return (
    <div className="glass-strong flex flex-col gap-2 rounded-xl p-3 shadow-[0_6px_16px_rgba(0,0,0,0.25)]">
      <TextArea value={text} onChange={e => setText(e.target.value)} rows={compact ? 3 : 4}
        placeholder={'Write or paste anything — a whole meeting dump is fine.\nFirst line becomes the title; file it later from the inbox.'} />
      <PendingStrip items={pending} onRemove={i => setPending(p => p.filter((_, n) => n !== i))} />
      <div className="flex flex-wrap items-center gap-2">
        {/* No `capture` attribute on purpose: iOS then offers Camera AND Photo
            Library, so an existing photo can be attached, not just a new one. */}
        <input ref={photoRef} type="file" accept="image/*" multiple hidden onChange={onPhoto} />
        <Btn kind="ghost" type="button" disabled={busy} onClick={() => photoRef.current?.click()}>+ Photo</Btn>
        <VoiceRecorder disabled={busy} onDone={p => setPending(q => [...q, p])} />
        <span className="text-[11px] text-muted">
          {text.trim() ? `${text.trim().split('\n').length} line${text.trim().split('\n').length === 1 ? '' : 's'}` : 'Text optional when you attach'}
        </span>
        <Btn className="ml-auto" onClick={() => void capture()} busy={busy} disabled={!text.trim() && !pending.length}>Capture</Btn>
      </div>
    </div>
  )
}

/** 2c: a note's photos and voice memos. Photos render inline, voice plays in
 *  place, and an unrenderable format says so rather than showing a broken
 *  image. `onChange` lets the page refetch after a removal. */
/** Attach to a note that already exists: uploads immediately, no staging —
 *  the note is there to receive them. */
export function AttachToNote({ noteId, onChange }: { noteId: number; onChange?: () => void }) {
  const toast = useToast(); const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const photoRef = useRef<HTMLInputElement>(null)
  const send = async (items: Pending[]) => {
    if (!items.length) return
    setBusy(true)
    try {
      for (const p of items) await uploadAttachment(noteId, p)
      toast(items.length === 1 ? 'Attached' : `Attached ${items.length} files`)
      qc.invalidateQueries(); onChange?.()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input ref={photoRef} type="file" accept="image/*" multiple hidden
        onChange={e => {
          const files = Array.from(e.target.files ?? [])
          e.target.value = ''
          void send(files.map(f => ({ file: f, name: f.name, kind: 'photo' as const, mime: f.type })))
        }} />
      <Btn kind="ghost" type="button" busy={busy} onClick={() => photoRef.current?.click()}>+ Photo</Btn>
      <VoiceRecorder disabled={busy} onDone={p => void send([p])} />
    </div>
  )
}

export function NoteAttachments({ items, onChange }: { items: NoteAttachment[]; onChange?: () => void }) {
  const toast = useToast(); const qc = useQueryClient()
  const [busy, setBusy] = useState<number | null>(null)
  if (!items.length) return null
  const remove = async (a: NoteAttachment) => {
    setBusy(a.id)
    try {
      await del(`/api/attachment/${a.id}`)
      toast('Attachment removed'); qc.invalidateQueries(); onChange?.()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(null) }
  }
  return (
    <div className="flex flex-col gap-3" data-note-attachments>
      {items.map(a => (
        <figure key={a.id} className="flex min-w-0 flex-col gap-1 rounded-lg border border-line p-2">
          {a.kind === 'photo' ? (
            a.renderable
              ? <a href={attachmentUrl(a.id)} target="_blank" rel="noreferrer">
                  <img src={attachmentUrl(a.id)} alt={a.orig_name ?? 'photo'}
                       loading="lazy" className="max-h-80 w-full rounded object-contain" />
                </a>
              // HEIC/HEIF: no browser paints these. Say so and offer the file
              // rather than rendering a broken-image icon on the owner's phone.
              : <div className="rounded bg-glass p-3 text-[13px] text-muted">
                  This format ({a.mime}) can’t be shown in a browser.{' '}
                  <a className="underline" href={attachmentUrl(a.id)} download={a.orig_name ?? undefined}>Download</a>
                </div>
          ) : (
            <audio controls preload="metadata" src={attachmentUrl(a.id)} className="w-full" />
          )}
          <figcaption className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
            <span className="min-w-0 truncate">{a.kind === 'voice' ? '🎤' : '🖼'} {a.orig_name ?? a.stored_name}</span>
            {!!a.duration_ms && <span>{Math.round(a.duration_ms / 1000)}s</span>}
            <span>{Math.max(1, Math.round(a.bytes / 1024))} KB</span>
            <button type="button" disabled={busy === a.id} onClick={() => void remove(a)}
              className="ml-auto underline underline-offset-2 hover:text-fg disabled:opacity-60">
              {busy === a.id ? 'removing…' : 'remove'}
            </button>
          </figcaption>
        </figure>
      ))}
    </div>
  )
}

/** Pending librarian proposals for ONE note — the note page banner. The
 * decision buttons live here too so the owner can decide wherever they are. */
export function ProposalBanner({ noteId }: { noteId: number }) {
  const qc = useQueryClient(); const toast = useToast()
  const q = useQuery({
    queryKey: ['note-proposals', noteId],
    queryFn: () => get<{ proposals: Proposal[] }>(`/api/proposals?status=pending&note_id=${noteId}`),
    enabled: Number.isFinite(noteId),
  })
  const [busy, setBusy] = useState(false)
  const [rejecting, setRejecting] = useState<number | null>(null)
  const [feedback, setFeedback] = useState('')
  const rows = q.data?.proposals ?? []
  if (!rows.length) return null
  const decide = async (id: number, action: 'approve' | 'reject') => {
    setBusy(true)
    try {
      await post(`/api/proposal/${id}/${action}`, action === 'reject' ? { feedback } : undefined)
      toast(action === 'approve' ? 'Proposal applied' : 'Rejected — feedback saved for the librarian')
      setRejecting(null); setFeedback(''); qc.invalidateQueries()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <div className="mb-4 flex flex-col gap-3">
      {rows.map(p => (
        <div key={p.id} className="rounded-xl border border-working/50 bg-working/10 p-3" data-proposal-banner>
          <div className="flex flex-wrap items-center gap-2">
            <Label>Librarian proposed · {KIND_LABEL[p.kind]}</Label>
            <span className="ml-auto font-mono text-[10px] text-muted">#{p.id}</span>
          </div>
          {p.summary && <p className="mt-1 text-sm">{p.summary}</p>}
          <ProposalPayloadView p={p} compact />
          {rejecting === p.id ? (
            <div className="mt-3 flex flex-col gap-2">
              <TextArea rows={2} value={feedback} onChange={e => setFeedback(e.target.value)}
                placeholder="Why not? The librarian reads this before re-proposing." />
              <div className="flex gap-2">
                <Btn kind="warn" onClick={() => void decide(p.id, 'reject')} busy={busy}>Reject proposal</Btn>
                <Btn kind="ghost" onClick={() => setRejecting(null)}>Cancel</Btn>
              </div>
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              <Btn onClick={() => void decide(p.id, 'approve')} busy={busy}>{approveLabel(p)}</Btn>
              <Btn kind="ghost" onClick={() => { setRejecting(p.id); setFeedback('') }}>Reject…</Btn>
              <Link to="/brain/review" className="ml-auto self-center text-[11px] text-accent-2 hover:underline">Review queue →</Link>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/** Library tree counts used by the Home sidebar too. */
export function useBrainCounts() {
  const tree = useNotesTree()
  return { tree, inbox: tree.data?.counts?.inbox ?? 0, review: tree.data?.counts?.proposals_pending ?? 0 }
}
