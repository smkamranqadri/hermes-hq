/** Second Brain shared pieces: sub-nav, note rows, file/edit + graduation
 * modals (intent/SecondBrainPlan.md P1). Notes never convert — "New task" /
 * "New reminder" create linked items and the note stays a note. */
import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { NOTES_REFETCH, ago, attachmentUrl, del, get, post, postForm, useAreas, useNoteTags, useNotesTree, useProjects, useRoster, type Area, type Note, type NoteAttachment, type NoteFull, type Proposal, type ProposalPayload, type SplitPart } from '../api'
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
      {/* The vault is its own Library folder. No count: a badge would leak how
          much is in there to anyone glancing at the phone. */}
      <NavLink to="/brain/vault" className={pill}>Vault</NavLink>
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

export const KIND_LABEL: Record<Proposal['kind'], string> = { split: 'split', file: 'file', contradiction: 'contradiction', new_task: 'new task', append_entry: 'add thought' }

/** Record-keyed so adding a proposal kind fails compilation here instead of
 * silently inheriting another kind's button copy. */
export function approveLabel(p: Proposal): string {
  const labels: Record<Proposal['kind'], string> = {
    split: `Approve split (${p.payload?.parts?.length ?? 0} notes)`,
    file: p.payload?.archive ? 'Approve → archive' : 'Approve filing',
    contradiction: 'Mark both disputed',
    new_task: 'Create task & link',
    append_entry: `Add thought to note #${p.payload?.target_note_id ?? '?'}`,
  }
  return labels[p.kind]
}

/** Kind-specific payload rendering, shared by the Review queue and the
 * note-page banner so every surface tells the same story. */
export function ProposalPayloadView({ p, compact = false }: { p: Proposal; compact?: boolean }) {
  const areas = useAreas(); const projects = useProjects()
  const location = useLocation()
  const fromState = { from: location.pathname + location.search }
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
  // Area coinage is a bigger commitment than a tag — approving creates a new
  // top-level place in the Library — so it renders as loudly as the tags do.
  const na = p.payload?.new_area
  const newArea = na ? (
    <span className={pillCls('working')}>
      new area: {areaLabel(areas.data?.areas, na.parent_id ?? null) ? `${areaLabel(areas.data?.areas, na.parent_id ?? null)} / ` : ''}{na.name}
    </span>
  ) : null
  // A filed note always leaves the inbox, so file gets NO inbox-fallback
  // chip; an unfiled split part genuinely lands back in the inbox, so split
  // parts DO — that chip is the owner's pre-approval warning.
  if (p.kind === 'file' && p.payload) return <div className="mt-2 flex flex-wrap items-center gap-1.5">{chips(p.payload)}{newArea}{newTags}</div>
  if (p.kind === 'split') return (
    <ol className="mt-2 flex flex-col gap-1.5">
      {(p.payload?.parts ?? []).map((part: SplitPart, i: number) => (
        <li key={i} className={clsx('rounded-lg border border-line-subtle bg-inset px-3', compact ? 'py-1.5' : 'py-2')}>
          <div className={clsx('flex flex-wrap items-center gap-1.5', compact && 'text-xs')}>
            <span className={clsx('font-medium', compact ? '' : 'text-xs')}>{part.title}</span>
            {chips(part, true)}
          </div>
          {!compact && plainExcerpt(part.body) && <p className="mt-1 line-clamp-2 text-[11px] text-muted">{plainExcerpt(part.body)}</p>}
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
        <Link to={`/brain/note/${p.payload?.other_note_id}`} state={fromState} className="font-medium hover:text-accent-2">note #{p.payload?.other_note_id}</Link>
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
      {plainExcerpt(p.payload?.description) && <p className="mt-1 line-clamp-2 text-muted">{plainExcerpt(p.payload?.description)}</p>}
      <p className="mt-1 text-[11px] text-muted">Creates a real linked task — the note stays a note.</p>
    </div>
  )
  if (p.kind === 'append_entry') return (
    <div className="mt-2 rounded-lg border border-line-subtle bg-inset px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip tone="accent">thought</Chip>
        <span className="text-muted">onto</span>
        <Link to={`/brain/note/${p.payload?.target_note_id}`} state={fromState} className="font-medium hover:text-accent-2">note #{p.payload?.target_note_id}</Link>
        {p.payload?.archive_source === false && <Chip>source stays</Chip>}
      </div>
      {p.payload?.body
        ? <p className={clsx('mt-1 whitespace-pre-wrap text-muted', !compact && 'line-clamp-4')}>{p.payload.body}</p>
        : <p className="mt-1 text-[11px] text-muted">Appends this capture verbatim.</p>}
      {/* Entries have no delete path anywhere — say so before the click. */}
      <p className="mt-1 text-[11px] text-needsyou">A thought cannot be removed once added.
        {p.payload?.archive_source !== false && ' The source capture is archived.'}</p>
    </div>
  )
  return null
}

/** A readable one-line excerpt of a markdown body, for the clamped previews in
 * list rows and proposal cards.
 *
 * The note PAGE renders markdown properly (see chat/Markdown.tsx) — this is for
 * the places that show two clamped lines, where raw markdown is actively
 * hostile: 247 of 295 notes carry markdown, and most of the tutorial notes open
 * with a fenced code block, so every row read "```bash sudo apt update ```"
 * instead of a sentence (owner report, 2026-09-09).
 *
 * Fenced code is DROPPED rather than unwrapped — a preview of shell commands
 * tells you nothing about what the note is. Everything else is unwrapped to its
 * text so the first real prose wins.
 */
export function plainExcerpt(body: string | null | undefined, max = 200) {
  if (!body) return ''
  let t = body
    .replace(/```[\s\S]*?```/g, ' ')        // fenced code: drop entirely
    .replace(/~~~[\s\S]*?~~~/g, ' ')
    .replace(/^---[\s\S]*?^---/m, ' ')      // yaml frontmatter
    .replace(/`([^`]*)`/g, '$1')             // inline code: keep the text
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')  // images
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1') // links -> their text
    .replace(/\[\[([^\]]+)\]\]/g, '$1')     // wikilinks
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')      // heading markers
    .replace(/^\s*>+\s?/gm, '')             // blockquote markers
    .replace(/^\s*[-*+]\s+\[[ xX]\]\s*/gm, '') // task-list markers
    .replace(/^\s*[-*+]\s+/gm, '')          // bullet markers
    .replace(/^\s*\d+[.)]\s+/gm, '')        // numbered markers
    .replace(/^\s*([-*_]\s*){3,}$/gm, ' ')   // horizontal rules
    .replace(/\*\*([^*]+)\*\*/g, '$1')      // bold
    .replace(/(^|\W)[*_]([^*_]+)[*_](\W|$)/g, '$1$2$3') // emphasis
    .replace(/\s+/g, ' ')
    .trim()
  return t.length > max ? t.slice(0, max).trimEnd() + '…' : t
}

export function areaLabel(areas: Area[] | undefined, id: number | null) {
  if (!id || !areas) return null
  const a = areas.find(x => x.id === id)
  if (!a) return null
  const parent = a.parent_id ? areas.find(x => x.id === a.parent_id) : null
  return parent ? `${parent.name} / ${a.name}` : a.name
}

/** True when the note's area has been RETIRED. Retiring is how an area is
 * removed (there is no delete), and its archived notes keep pointing at it —
 * so the row still names the home, just muted, instead of dropping the chip
 * and reading as "never filed". Same provenance treatment `authored_by` got. */
export function areaRetired(areas: Area[] | undefined, id: number | null) {
  if (!id || !areas) return false
  return !!areas.find(x => x.id === id)?.archived
}

/** The area chip for a list row: accent while the area is live, muted and
 * marked once it is retired. */
export function AreaChip({ areas, id }: { areas: Area[] | undefined; id: number | null }) {
  const label = areaLabel(areas, id)
  if (!label) return null
  return areaRetired(areas, id)
    ? <Chip><span title="This area was retired — the note keeps its filing">{label} · retired</span></Chip>
    : <Chip tone="accent">{label}</Chip>
}

/** One note in a list — Library results, project tab, Home recents. */
export function NoteRow({ n, areas, showBody = true }: { n: Note; areas?: Area[]; showBody?: boolean }) {
  const location = useLocation()
  // Carry where we came from so archiving (on the note page) can return here
  // instead of always bouncing to the unfiltered Library.
  return (
    <Link to={`/brain/note/${n.id}`} state={{ from: location.pathname + location.search }} className="glass block rounded-xl px-4 py-3 hover:bg-raised">
      <div className="flex flex-wrap items-center gap-2">
        {n.type !== 'note' && <span className={clsx('font-mono text-[10px] uppercase tracking-wider', TYPE_TONE[n.type])}>{n.type}</span>}
        {!!n.pinned && <span className="font-mono text-[10px] text-needsyou" title="Pinned">★</span>}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{n.title}</span>
        {(n.entry_count ?? 0) > 0 && <span className="font-mono text-[10px] text-muted">{n.entry_count} thought{n.entry_count === 1 ? '' : 's'}</span>}
        <span className="font-mono text-[10px] text-muted">{ago(n.updated_at ?? n.created_at)}</span>
      </div>
      {showBody && plainExcerpt(n.body) && <p className="mt-1 line-clamp-2 text-xs text-muted">{plainExcerpt(n.body)}</p>}
      <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5">
        <AreaChip areas={areas} id={n.area_id} />
        {/* The note page shows area + project + tags + authored_by — a row
            missing the project chip read as "not all tags shown" (owner
            report). Same plain-Chip look as the note page, no Link inside
            this row's own Link. */}
        {n.project_name && <Chip tone="accent">{n.project_name}</Chip>}
        {n.tags.map(t => <Chip key={t}>{t}</Chip>)}
        {n.status === 'archived' && <Chip>archived</Chip>}
        {/* owner authorship is the default and would be noise on every row —
            only import/librarian authorship is worth a chip here. */}
        {n.authored_by !== 'owner' && <span className="font-mono text-[10px] text-muted" title={PROVENANCE_HINT[n.authored_by] ?? ''}>{PROVENANCE[n.authored_by] ?? n.authored_by}</span>}
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

/** `authored_by` is provenance, not a tag: who wrote the note's original text.
 *  The owner's own notes show nothing (the default); the other two read as a
 *  muted word with a hover hint, never as a chip in the tag row — the owner
 *  read the bare "import"/"owner" chips as tags he had not set. */
export const PROVENANCE: Record<string, string> = { import: 'imported', librarian: 'by the librarian' }
export const PROVENANCE_HINT: Record<string, string> = {
  import: 'Brought in by an import wave — the text is from the source, not typed here.',
  librarian: 'Written by the librarian agent (a wiki or playbook compiled from your notes).',
}
export const tagsFrom = (s: string) => s.split(',').map(t => t.trim()).filter(Boolean)

const TAG_SUGGEST_CAP = 24

/** Tag input for the three filing surfaces (FileNoteModal, EditProposalModal
 * file kind + split parts): free text stays first-class — comma/Enter mints
 * a chip, so the owner can still coin a brand-new tag — with the registered
 * taxonomy offered as tappable suggestion chips (counts included), filtered
 * by what's typed. Tapping a suggestion toggles it in/out of the selection;
 * the selection also renders as its own removable-chip row above the input,
 * since that's the row the owner scans to see what's about to be saved.
 * Value is string[] in and out — every save path this replaces already
 * built/consumed a string[] via tagsFrom(), so callers just drop that split.
 * NOT the system-wide tag combobox (explicitly deferred by the owner) — this
 * stays local to these brain.tsx filing surfaces. */
export function TagPicker({ value, onChange, placeholder = 'payments, 1:1' }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string
}) {
  const tags = useNoteTags()
  const [draft, setDraft] = useState('')
  const [showAll, setShowAll] = useState(false)
  const add = (raw: string) => {
    const t = raw.trim()
    if (t && !value.includes(t)) onChange([...value, t])
  }
  const remove = (t: string) => onChange(value.filter(x => x !== t))
  const toggle = (t: string) => value.includes(t) ? remove(t) : add(t)
  const commitDraft = () => {
    const parts = tagsFrom(draft)
    if (!parts.length) return
    const next = [...value]
    for (const t of parts) if (!next.includes(t)) next.push(t)
    onChange(next)
    setDraft('')
  }
  const q = draft.trim().toLowerCase()
  const all = tags.data?.tags ?? []
  const matches = q ? all.filter(t => t.tag.toLowerCase().includes(q)) : all
  const shown = showAll ? matches : matches.slice(0, TAG_SUGGEST_CAP)
  return (
    <div className="flex flex-col gap-2">
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5" aria-label="Selected tags">
          {value.map(t => (
            <button key={t} type="button" onClick={() => remove(t)}
              className="inline-flex items-center gap-1 rounded-full border border-accent/50 bg-accent/15 px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-accent-2">
              {t}<span aria-hidden="true">✕</span>
            </button>
          ))}
        </div>
      )}
      <TextInput value={draft} placeholder={placeholder} aria-label="Add tags" className="text-base"
        onChange={e => setDraft(e.target.value)}
        onBlur={() => { if (draft.trim()) commitDraft() }}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commitDraft() }
          else if (e.key === 'Backspace' && !draft && value.length) remove(value[value.length - 1])
        }} />
      {matches.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {shown.map(t => (
            // onMouseDown preventDefault: without it, tapping a suggestion
            // while text is still typed fires the input's onBlur FIRST
            // (committing the typed draft as its own tag), then the click —
            // so typing "pay" and tapping "payments" added BOTH, and the
            // re-filtered list could shift the chip out from under the tap.
            <button key={t.tag} type="button" onMouseDown={e => e.preventDefault()} onClick={() => toggle(t.tag)}
              aria-pressed={value.includes(t.tag)}
              className={clsx('inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-mono text-[11px]',
                value.includes(t.tag) ? 'border-accent/50 bg-accent/15 text-accent-2' : 'border-line text-muted hover:text-fg')}>
              {t.tag} <span className="opacity-70">{t.count}</span>
            </button>
          ))}
          {!showAll && matches.length > TAG_SUGGEST_CAP && (
            <button type="button" onClick={() => setShowAll(true)} className="text-[11px] text-accent-2 hover:underline">
              +{matches.length - TAG_SUGGEST_CAP} more…
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/** Two-level area picker — the ONE place the area optgroup tree is rendered
 * (FileNoteModal and the proposal editor both consume it). Callers pass
 * `useNotesTree()`'s areas (not `useAreas()`'s) so `note_count` is populated
 * and the option labels can say how full an area already is. */
export function AreaSelect({ value, onChange, areas }: { value: string; onChange: (v: string) => void; areas: Area[] }) {
  const roots = areas.filter(a => !a.parent_id)
  const cnt = (a: Area) => typeof a.note_count === 'number' ? ` (${a.note_count})` : ''
  return (
    <SelectInput value={value} onChange={e => onChange(e.target.value)} aria-label="Area">
      <option value="">— no area —</option>
      {roots.map(r => {
        const kids = areas.filter(a => a.parent_id === r.id)
        // A parentless area with no children is just an area — an optgroup
        // wrapping a single same-named option only doubled the name on screen.
        if (kids.length === 0) return <option key={r.id} value={r.id}>{r.name}{cnt(r)}</option>
        return (
          <optgroup key={r.id} label={`${r.name}${cnt(r)}`}>
            <option value={r.id}>{r.name}{cnt(r)}</option>
            {kids.map(a => <option key={a.id} value={a.id}>{r.name} / {a.name}{cnt(a)}</option>)}
          </optgroup>
        )
      })}
    </SelectInput>
  )
}

/** The selected area's filing rule, for surfaces that file. Empty string when
 * nothing is selected or the area has no description yet. */
export function areaDescription(areas: Area[] | undefined, id: string) {
  if (!id || !areas) return ''
  return (areas.find(a => a.id === Number(id))?.description ?? '').trim()
}

/** Project picker by id — shared for the same reason. */
/** Project options with the tree's live note counts merged in (tree lists only
 *  projects that hold notes; the rest stay countless rather than showing 0). */
export const withProjectCounts = (projects: { id: number; name: string }[], tree?: { id: number; note_count?: number }[]) =>
  projects.map(p => { const t = tree?.find(x => x.id === p.id); return t ? { ...p, note_count: t.note_count ?? 0 } : p })

export function ProjectSelect({ value, onChange, projects }: { value: string; onChange: (v: string) => void; projects: { id: number; name: string; note_count?: number }[] }) {
  return (
    <SelectInput value={value} onChange={e => onChange(e.target.value)} aria-label="Project">
      <option value="">— no project —</option>
      {projects.map(p => <option key={p.id} value={p.id}>{p.name}{typeof p.note_count === 'number' ? ` (${p.note_count})` : ''}</option>)}
    </SelectInput>
  )
}

/** File / edit metadata: area, project, tags, type — the manual filing path
 * until the librarian proposes these in Phase 2. Filing an inbox note also
 * moves it to active. */
export function FileNoteModal({ n, onClose }: { n: Note; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const tree = useNotesTree(); const projects = useProjects()
  const [f, setF] = useState({
    area_id: n.area_id ? String(n.area_id) : '', project_id: n.project_id ? String(n.project_id) : '',
    type: n.type as string, tags: n.tags,
  })
  const [busy, setBusy] = useState(false)
  const home = !!f.area_id || !!f.project_id
  // A note may only END UP active with a home (core/wm_store.py update_note).
  // An inbox note saved with no home just stays inbox — a tag- or type-only
  // edit on a fresh capture must never be blocked. An already-active note
  // clearing both fields down to nothing WOULD go homeless-active, which the
  // store refuses; block Save there instead of round-tripping a 409.
  const blocked = n.status === 'active' && !home
  const save = async () => {
    setBusy(true)
    try {
      await post(`/api/note/${n.id}/edit`, {
        area_id: f.area_id ? Number(f.area_id) : undefined, clear_area: !f.area_id,
        project_id: f.project_id ? Number(f.project_id) : undefined, clear_project: !f.project_id,
        type: f.type, tags: f.tags,
        status: n.status === 'inbox' && home ? 'active' : undefined,
      })
      toast(n.status === 'inbox' && home ? 'Filed' : 'Saved'); qc.invalidateQueries(); onClose()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title={n.status === 'inbox' ? `File “${n.title}”` : 'Edit filing'} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Field label="Area">
          <AreaSelect value={f.area_id} onChange={v => setF(x => ({ ...x, area_id: v }))} areas={tree.data?.areas ?? []} />
          {/* The description says what belongs here and what does not — the
              same rule the librarian files by. Showing it at the moment of
              filing is the point; omitted when the area has none yet. */}
          {areaDescription(tree.data?.areas, f.area_id) && (
            <p className="mt-1 text-xs text-muted">{areaDescription(tree.data?.areas, f.area_id)}</p>
          )}
        </Field>
        <Field label="Project" hint="Project-linked notes show on that project's page.">
          <ProjectSelect value={f.project_id} onChange={v => setF(x => ({ ...x, project_id: v }))} projects={withProjectCounts(projects.data?.projects ?? [], tree.data?.projects)} />
        </Field>
        <Field label="Type">
          <SelectInput value={f.type} onChange={e => setF(x => ({ ...x, type: e.target.value }))}>
            {['note', 'playbook', 'wiki'].map(t => <option key={t}>{t}</option>)}
          </SelectInput>
        </Field>
        <Field label="Tags"><TagPicker value={f.tags} onChange={v => setF(x => ({ ...x, tags: v }))} /></Field>
        {n.status === 'inbox'
          ? !home && <p className="text-xs text-muted">Pick an area or a project to file it; saving without one keeps it in the inbox.</p>
          : blocked && <p className="text-xs text-muted">A note needs an area or a project to be filed.</p>}
        <div className="flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn onClick={() => void save()} busy={busy} disabled={blocked}>{n.status === 'inbox' ? 'File note' : 'Save'}</Btn></div>
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
    // Declared inline rather than in api.ts, which is exactly how it escaped
    // the polling sweep: without this, a proposal the librarian files while
    // the owner is reading the note never appears on the page it is about.
    refetchInterval: NOTES_REFETCH,
  })
  const [busy, setBusy] = useState(false)
  const [rejecting, setRejecting] = useState<number | null>(null)
  const [editing, setEditing] = useState<Proposal | null>(null)
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
      {editing && <EditProposalModal p={editing} onClose={() => setEditing(null)} />}
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
              {/* This page is where the owner reads enough to WANT an edit;
                * without it here, deciding "nearly right" meant reading the
                * note, navigating to the queue, and finding the row again. */}
              {EDITABLE[p.kind] && <Btn kind="ghost" onClick={() => setEditing(p)}>Edit…</Btn>}
              <Btn kind="ghost" onClick={() => { setRejecting(p.id); setFeedback('') }}>Reject…</Btn>
              <Link to="/brain/review" className="ml-auto self-center text-[11px] text-accent-2 hover:underline">Review queue →</Link>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/** Which kinds the owner may edit before approving. Record-keyed (not an
 * array) so a new proposal kind cannot compile without deciding this.
 * Shared by the Review queue AND the note-page banner: the note page is
 * where the owner reads enough to WANT an edit, so an action available in
 * one place and not the other just means a round trip. */
export const EDITABLE: Record<Proposal['kind'], boolean> = { split: true, file: true, contradiction: false, new_task: true, append_entry: false }

export function EditProposalModal({ p, onClose }: { p: Proposal; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const tree = useNotesTree(); const projects = useProjects(); const roster = useRoster()
  const pay = p.payload ?? {}
  const [file, setFile] = useState({
    area_id: pay.area_id ? String(pay.area_id) : '', project_id: pay.project_id ? String(pay.project_id) : '',
    type: pay.type ?? '', tags: pay.tags ?? [] as string[], archive: !!pay.archive,
    // Area coinage the librarian declared. Editable here so the owner can
    // rename it or drop it for an existing area — without this field the
    // modal would silently discard the proposed area on every edit.
    new_area: pay.new_area?.name ?? '', new_area_parent: pay.new_area?.parent_id,
  })
  const [task, setTask] = useState({
    title: pay.title ?? '', description: pay.description ?? '',
    project_id: pay.project_id ? String(pay.project_id) : '', assignee: pay.assignee ?? 'owner',
  })
  const [parts, setParts] = useState<SplitPart[]>(() => (pay.parts ?? []).map(x => ({ ...x })))
  // React key per part, kept in lockstep with `parts` but never sent in the
  // payload (SplitPart has no id field). Keying the row by INDEX meant
  // dropping part 1 shifted part 2 into slot 1 — React then reused part 1's
  // DOM/state for it, so its TagPicker kept part 1's typed-but-uncommitted
  // draft text instead of starting clean for the part that's actually there.
  const partKeySeed = useRef(0)
  const [partKeys, setPartKeys] = useState<string[]>(() => parts.map(() => String(partKeySeed.current++)))
  const [keepOriginal, setKeepOriginal] = useState(pay.archive_original === false)
  const [busy, setBusy] = useState(false)
  const setPart = (i: number, patch: Partial<SplitPart>) => setParts(ps => ps.map((x, j) => j === i ? { ...x, ...patch } : x))
  const buildPayload = (): ProposalPayload => {
    if (p.kind === 'file') {
      const out: ProposalPayload = {}
      if (file.area_id) out.area_id = Number(file.area_id)
      // Exclusive by design (the store rejects both): picking a real area is
      // the owner saying "no, put it here" — that wins over the coinage.
      else if (file.new_area.trim()) {
        out.new_area = { name: file.new_area.trim() }
        if (file.new_area_parent != null) out.new_area.parent_id = file.new_area_parent
      }
      if (file.project_id) out.project_id = Number(file.project_id)
      if (file.type) out.type = file.type as ProposalPayload['type']
      if (file.tags.length) out.tags = file.tags
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
  const projectList = withProjectCounts(projects.data?.projects ?? [], tree.data?.projects)
  // A file proposal may only END UP active with a home (core/wm_store.py
  // update_note / _validate_proposal_payload) — archiving is the one
  // exception. A file payload never CLEARS the note's existing home, only
  // adds one, so a note that already has an area/project stays homed
  // regardless of what this edit touches. Block the approve button the same
  // way FileNoteModal blocks Save, instead of round-tripping a 409.
  const noteAlreadyHomed = !!p.note_area_id || !!p.note_project_id
  const fileHomeless = p.kind === 'file' && !noteAlreadyHomed
    && !file.area_id && !file.new_area.trim() && !file.project_id && !file.archive
  return (
    <Modal title={`Edit ${KIND_LABEL[p.kind]} proposal #${p.id}`} onClose={onClose}>
      <div className="flex flex-col gap-3">
        {p.kind === 'file' && (
          <>
            <Field label="Area"><AreaSelect value={file.area_id} onChange={v => setFile(x => ({ ...x, area_id: v }))} areas={tree.data?.areas ?? []} /></Field>
            {(file.new_area || !file.area_id) && (
              <Field label="…or a new area"
                     hint={file.area_id ? 'Ignored — you picked an existing area above.'
                                        : 'Approving CREATES this area. The note needs an area or a project (or Archive) to be filed.'}>
                <TextInput value={file.new_area} placeholder="e.g. DIY"
                           onChange={e => setFile(x => ({ ...x, new_area: e.target.value }))} />
              </Field>
            )}
            <Field label="Project"><ProjectSelect value={file.project_id} onChange={v => setFile(x => ({ ...x, project_id: v }))} projects={projectList} /></Field>
            <Field label="Tags"><TagPicker value={file.tags} onChange={v => setFile(x => ({ ...x, tags: v }))} /></Field>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={file.archive} onChange={e => setFile(x => ({ ...x, archive: e.target.checked }))} />
              Send to Archive (junk/museum — stays searchable)
            </label>
            {fileHomeless && <p className="text-xs text-muted">A note needs an area or a project to be filed.</p>}
          </>
        )}
        {p.kind === 'new_task' && (
          <>
            <Field label="Title"><TextInput value={task.title} onChange={e => setTask(x => ({ ...x, title: e.target.value }))} /></Field>
            <Field label="Description"><TextArea rows={2} value={task.description} onChange={e => setTask(x => ({ ...x, description: e.target.value }))} /></Field>
            <Field label="Project" hint="Defaults to the note's project when left empty."><ProjectSelect value={task.project_id} onChange={v => setTask(x => ({ ...x, project_id: v }))} projects={projectList} /></Field>
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
                <div key={partKeys[i] ?? i} className="rounded-lg border border-line-subtle bg-inset p-2">
                  <div className="flex items-center gap-2">
                    <TextInput value={part.title} onChange={e => setPart(i, { title: e.target.value })} aria-label={`Part ${i + 1} title`} />
                    {parts.length > 1 && (
                      <button type="button" title="Drop this part" className="shrink-0 text-muted hover:text-needsyou"
                        onClick={() => { setParts(ps => ps.filter((_, j) => j !== i)); setPartKeys(ks => ks.filter((_, j) => j !== i)) }}>✕</button>
                    )}
                  </div>
                  {part.body && <p className="mt-1 line-clamp-1 text-[11px] text-muted">{part.body}</p>}
                  <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                    <div className="min-w-0 flex-1"><AreaSelect value={part.area_id ? String(part.area_id) : ''} onChange={v => setPart(i, { area_id: v ? Number(v) : undefined })} areas={tree.data?.areas ?? []} /></div>
                    <div className="min-w-0 flex-1"><ProjectSelect value={part.project_id ? String(part.project_id) : ''} onChange={v => setPart(i, { project_id: v ? Number(v) : undefined })} projects={projectList} /></div>
                  </div>
                  <div className="mt-2"><TagPicker value={part.tags ?? []} onChange={v => setPart(i, { tags: v })} placeholder="tags, comma-separated" /></div>
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
          <Btn onClick={() => void save()} busy={busy}
               disabled={(p.kind === 'new_task' && !task.title.trim()) || fileHomeless}>Approve with edits</Btn>
        </div>
      </div>
    </Modal>
  )
}

/** Library tree counts used by the Home sidebar too. */
export function useBrainCounts() {
  const tree = useNotesTree()
  return { tree, inbox: tree.data?.counts?.inbox ?? 0, review: tree.data?.counts?.proposals_pending ?? 0 }
}
