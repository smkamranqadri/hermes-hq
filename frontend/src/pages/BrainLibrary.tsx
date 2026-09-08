/** Library: folder tree (areas → sub-areas, projects, types, archive) +
 * global search over every note (FTS). Selection lives in the URL. */
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import clsx from 'clsx'
import { useAreas, useNoteTags, useNotes, useNotesTree } from '../api'
import { PageHeader } from '../components/GlassCard'
import { BrainSubNav, NoteRow, useBrainCounts } from '../components/brain'
import { Empty, Input, Label, Loading, Select } from '../components/ui'
import { usePageTitle } from '../usePageTitle'

function TreeRow({ label, count, depth = 0, active, onClick, mono = false }: {
  label: string; count?: number; depth?: number; active?: boolean; onClick: () => void; mono?: boolean
}) {
  return (
    <button onClick={onClick} style={{ paddingLeft: 8 + depth * 16 }}
      className={clsx('flex w-full items-center gap-2 rounded-lg py-1.5 pr-2 text-left text-[13px]',
        active ? 'border border-accent/50 bg-accent/15 text-fg' : 'border border-transparent text-muted hover:text-fg')}>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {typeof count === 'number' && <span className={clsx('font-mono text-[10px]', mono ? '' : 'text-muted')}>{count}</span>}
    </button>
  )
}

export function BrainLibrary() {
  usePageTitle('Library')
  const [sp, setSp] = useSearchParams()
  const areaSel = sp.get('area'); const projSel = sp.get('project'); const typeSel = sp.get('type'); const view = sp.get('view')
  const tagSel = sp.get('tag')
  const q = sp.get('q') ?? ''
  const [draft, setDraft] = useState(q)
  const { inbox, review } = useBrainCounts()
  const tree = useNotesTree()
  const areas = useAreas()
  const tags = useNoteTags()
  // `arch` is the ONLY door into the museum — `view=archived` used to be a
  // second one, reading as a synonym of the Active/Archive refine below.
  // Old links (or anything still emitting view=archived) get normalised to
  // arch=1 so they land on the same list instead of a dead URL.
  useEffect(() => {
    if (sp.get('view') === 'archived') {
      const n = new URLSearchParams(sp)
      n.delete('view'); n.set('arch', '1')
      setSp(n, { replace: true })
    }
  }, [sp, setSp])
  const pick = (k: 'area' | 'project' | 'type' | 'view', v: string | null) => {
    const n = new URLSearchParams()
    if (v) n.set(k, v)
    if (q) n.set('q', q)
    // arch/tag are refine-level, not top-level — every pick carries them
    // forward. project belongs to an area, so it only drops when the area
    // itself changes; a type/view/project pick leaves it alone. Exception:
    // an inbox note is never archived, so picking Inbox drops arch — else
    // Archive -> Inbox landed on ?view=inbox&arch=1 with the refine hidden
    // and the query silently asking for archived inbox notes (none exist).
    const arch = sp.get('arch'); if (arch && !(k === 'view' && v === 'inbox')) n.set('arch', arch)
    const tag = sp.get('tag'); if (tag) n.set('tag', tag)
    if (k !== 'area' && k !== 'project') {
      const proj = sp.get('project'); if (proj) n.set('project', proj)
    }
    setSp(n, { replace: true })
  }
  // 2b-ii sub-filters: tag (anywhere) and project (within an area) REFINE the
  // current selection instead of replacing it.
  const refine = (k: 'tag' | 'project' | 'arch', v: string | null) => {
    const n = new URLSearchParams(sp)
    v ? n.set(k, v) : n.delete(k)
    setSp(n, { replace: true })
  }
  // The default view hides the museum. `status` holds ONE value, so "not
  // archived" (inbox + active) needs its own flag — without it the default
  // listed archived notes beside live ones under an option labelled "Active".
  // Archive is a REFINE toggle beside tag/project, not a separate view: "All
  // notes" stays the default selection and simply hides the museum unless the
  // owner asks for it. The toggle means Active (hide the museum) vs Archive
  // (ONLY the museum, still scoped to area/project/tag/type/q) — never a mix.
  // arch wins over view: it is the one archive door, so it overrides Filed's
  // status=active the same way it overrides the default's exclude_archived.
  // Recognise the legacy view=archived synchronously too — the redirect
  // effect above only rewrites the URL, it must not gate the first query,
  // or a cold view=archived link would fetch the wrong (active) list once.
  const showArch = sp.get('arch') === '1' || view === 'archived'
  const archFilter = showArch ? { status: 'archived' } : { exclude_archived: true }
  const scoped = { area_id: areaSel ? Number(areaSel) : undefined, project_id: projSel ? Number(projSel) : undefined, type: typeSel ?? undefined, tag: tagSel ?? undefined, limit: 100 }
  const filters = q
    ? { ...scoped, q, ...archFilter }
    : showArch ? { ...scoped, status: 'archived' }
    : view === 'inbox' ? { ...scoped, status: 'inbox' }
    : view === 'filed' ? { ...scoped, status: 'active' }
    : { ...scoped, exclude_archived: true }
  // Shown on every view except Inbox — an inbox note is never archived, so
  // the toggle would be a dead control there. Stays live while searching.
  const showArchRefine = view !== 'inbox'
  const notes = useNotes(filters)
  const roots = (tree.data?.areas ?? []).filter(a => !a.parent_id)
  const children = (id: number) => (tree.data?.areas ?? []).filter(a => a.parent_id === id)
  const projName = projSel ? tree.data?.projects.find(p => String(p.id) === projSel)?.name : undefined
  const baseLabel = q ? `“${q}”` : view === 'inbox' ? 'Inbox' : view === 'filed' ? 'Filed' : typeSel ? typeSel + 's' : projSel && !areaSel ? projName ?? 'Project' : areaSel ? (tree.data?.areas ?? []).find(a => String(a.id) === areaSel)?.name ?? 'Area' : 'All notes'
  // Clearing must not also throw away the area/tag the owner had selected —
  // only the query goes.
  const clearSearch = () => {
    setDraft('')
    const n = new URLSearchParams(sp); n.delete('q'); setSp(n, { replace: true })
  }
  const searchScope = showArch ? 'the Archive'
    : areaSel ? ((tree.data?.areas ?? []).find(a => String(a.id) === areaSel)?.name ?? 'this area')
    : view === 'inbox' ? 'the Inbox'
    : projSel ? (projName ?? 'this project') : 'the Library'
  const selLabel = baseLabel
    + (areaSel && projSel ? ` · ${projName ?? 'project'}` : '')
    + (tagSel && !q && !view ? ` · #${tagSel}` : '')
    + (showArch ? ' · Archive' : '')
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="second-brain // library" title="Library" right={
        <div className="flex flex-wrap items-center gap-2">
          <BrainSubNav inbox={inbox} review={review} />
          <form className="relative" onSubmit={e => { e.preventDefault(); const n = new URLSearchParams(sp); draft ? n.set('q', draft) : n.delete('q'); setSp(n, { replace: true }) }}>
            {/* Search NARROWS the current selection, so the placeholder says
              * where it is looking. Escape and the × both clear it and leave
              * the owner exactly where they were. */}
            <Input value={draft} onChange={e => setDraft(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Escape') clearSearch() }}
                   placeholder={`Search ${searchScope}…`} className="w-full pr-8 sm:w-56" />
            {(draft || q) && (
              <button type="button" onClick={clearSearch} aria-label="Clear search"
                      title="Clear search (Esc)"
                      className="absolute right-2 top-1/2 -translate-y-1/2 px-1 text-muted hover:text-fg">✕</button>
            )}
          </form>
        </div>} />

      {/* Mobile: selects instead of the tree */}
      <div className="mb-4 flex flex-wrap gap-2 lg:hidden">
        <Select value={areaSel ?? ''} onChange={e => pick('area', e.target.value || null)}>
          <option value="">All areas</option>
          {(areas.data?.areas ?? []).map(a => <option key={a.id} value={a.id}>{a.parent_id ? '· ' : ''}{a.name}</option>)}
        </Select>
        <Select value={typeSel ?? ''} onChange={e => pick('type', e.target.value || null)}>
          <option value="">All types</option>
          {['note', 'playbook', 'wiki'].map(t => <option key={t}>{t}</option>)}
        </Select>
        <Select value={view ?? ''} onChange={e => pick('view', e.target.value || null)}>
          <option value="">All notes</option>
          <option value="inbox">Inbox</option>
          <option value="filed">Filed</option>
        </Select>
      </div>

      <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="hidden lg:block">
          <div className="glass rounded-xl p-2">
            {tree.isLoading && <Loading rows={4} />}
            <TreeRow label="All notes" active={!areaSel && !projSel && !typeSel && !view && !q} onClick={() => pick('area', null)} />
            <TreeRow label="Inbox" count={tree.data?.counts?.inbox} active={view === 'inbox'} onClick={() => pick('view', 'inbox')} />
            <p className="mt-2 px-2 font-mono text-[10px] uppercase tracking-widest text-muted">Areas</p>
            {roots.map(r => (
              <div key={r.id}>
                <TreeRow label={r.name} count={(r.note_count ?? 0) + children(r.id).reduce((s, c) => s + (c.note_count ?? 0), 0)} depth={1} active={areaSel === String(r.id)} onClick={() => pick('area', String(r.id))} />
                {children(r.id).map(c => (
                  <TreeRow key={c.id} label={c.name} count={c.note_count} depth={2} active={areaSel === String(c.id)} onClick={() => pick('area', String(c.id))} />
                ))}
              </div>
            ))}
            {(tree.data?.projects ?? []).length > 0 && <p className="mt-2 px-2 font-mono text-[10px] uppercase tracking-widest text-muted">Projects</p>}
            {(tree.data?.projects ?? []).map(p => (
              <TreeRow key={p.id} label={p.name} count={p.note_count} depth={1} active={projSel === String(p.id)} onClick={() => pick('project', String(p.id))} />
            ))}
            <p className="mt-2 px-2 font-mono text-[10px] uppercase tracking-widest text-muted">Collections</p>
            <TreeRow label="Playbooks" count={tree.data?.counts?.playbook ?? 0} depth={1} active={typeSel === 'playbook'} onClick={() => pick('type', 'playbook')} />
            <TreeRow label="Wiki" count={tree.data?.counts?.wiki ?? 0} depth={1} active={typeSel === 'wiki'} onClick={() => pick('type', 'wiki')} />
          </div>
        </div>

        <div className="min-w-0">
          <div className="mb-2 flex items-center justify-between">
            <Label>{selLabel}</Label>
            {notes.data && <span className="font-mono text-[10px] text-muted">{notes.data.notes.length} note{notes.data.notes.length === 1 ? '' : 's'}</span>}
          </div>
          {showArchRefine && (
            <div className="mb-3 flex flex-wrap gap-2">
              <Select value={showArch ? '1' : ''} onChange={e => refine('arch', e.target.value || null)} aria-label="Archive filter">
                <option value="">Active</option>
                <option value="1">Archive{typeof tree.data?.counts?.archived === 'number' ? ` (${tree.data.counts.archived})` : ''}</option>
              </Select>
              <Select value={tagSel ?? ''} onChange={e => refine('tag', e.target.value || null)} aria-label="Tag filter">
                <option value="">All tags</option>
                {(tags.data?.tags ?? []).map(t => <option key={t.tag} value={t.tag}>{t.tag} ({t.count})</option>)}
              </Select>
              {areaSel && (
                <Select value={projSel ?? ''} onChange={e => refine('project', e.target.value || null)} aria-label="Project filter">
                  <option value="">All projects</option>
                  {(tree.data?.projects ?? []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </Select>
              )}
            </div>
          )}
          {notes.isLoading && <Loading rows={5} />}
          {notes.isError && <Empty error title="Could not load notes" note={String(notes.error)} />}
          {notes.data && notes.data.notes.length === 0 && <Empty title={q ? 'No matches' : 'Nothing here yet'} note={q ? 'Try different words — search covers titles, bodies, entries and tags.' : 'Capture from Home, then file notes here.'} />}
          <div className="flex flex-col gap-2">
            {(notes.data?.notes ?? []).map(n => <NoteRow key={n.id} n={n} areas={areas.data?.areas} />)}
          </div>
        </div>
      </div>
    </section>
  )
}
