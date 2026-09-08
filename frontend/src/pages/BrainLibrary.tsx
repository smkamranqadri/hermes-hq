/** Library: folder tree (areas → sub-areas, projects, types, archive) +
 * global search over every note (FTS). Selection lives in the URL. */
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import clsx from 'clsx'
import { useAreas, useNoteFacets, useNotesPaged, useNotesTree } from '../api'
import { PageHeader } from '../components/GlassCard'
import { BrainSubNav, NoteRow, useBrainCounts } from '../components/brain'
import { Empty, Input, Label, Loading, Select } from '../components/ui'
import { Btn } from '../components/Modal'
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
  const scoped = { area_id: areaSel ? Number(areaSel) : undefined, project_id: projSel ? Number(projSel) : undefined, type: typeSel ?? undefined, tag: tagSel ?? undefined }
  const filters = q
    ? { ...scoped, q, ...archFilter }
    : showArch ? { ...scoped, status: 'archived' }
    : view === 'inbox' ? { ...scoped, status: 'inbox' }
    : view === 'filed' ? { ...scoped, status: 'active' }
    : { ...scoped, exclude_archived: true }
  // Shown on every view except Inbox — an inbox note is never archived, so
  // the toggle would be a dead control there. Stays live while searching.
  const showArchRefine = view !== 'inbox'
  // Slice E: paged, 50 at a time. `rows` is every page loaded so far and
  // `total` the unpaged count the server reports under the same filters.
  const notes = useNotesPaged(filters)
  const rows = notes.data?.pages.flatMap(pg => pg.notes) ?? []
  const total = notes.data?.pages[0]?.total ?? rows.length
  // LibraryPolishPlan Slice C: the refine selects (tag/project/area) list
  // only what's actually present in the CURRENT selection, with counts, so
  // the owner never picks a tag that empties the list. `filters` is exactly
  // the same scope the note list itself uses — the facets endpoint narrows
  // each dimension by every filter EXCEPT its own, so switching a tag/
  // project never narrows the very list it's choosing from. `limit` is
  // list-only paging lives in the hook, so `filters` is exactly what facets need.
  const facets = useNoteFacets(filters)
  // A facet selected via the URL might not appear in the current facet
  // result (e.g. the owner narrowed further and its count dropped to 0) —
  // keep it in the options list at count 0 so Clear stays reachable.
  const tagOptions = (() => {
    const base = facets.data?.tags ?? []
    return tagSel && !base.some(t => t.tag === tagSel) ? [...base, { tag: tagSel, count: 0 }] : base
  })()
  const projOptions = (() => {
    const base = facets.data?.projects ?? []
    if (!projSel || base.some(p => String(p.id) === projSel)) return base
    const name = tree.data?.projects.find(p => String(p.id) === projSel)?.name ?? 'project'
    return [...base, { id: Number(projSel), name, count: 0 }]
  })()
  // Archive gets a scoped count once facets load; while loading it falls
  // back to the library-WIDE archived count (the old behaviour) rather than
  // showing nothing. Active has no such global fallback (the tree never
  // tracked it), so it just omits the count until facets land.
  const activeLabel = 'Active' + (facets.data ? ` (${facets.data.active})` : '')
  const archiveLabel = 'Archive' + (facets.data ? ` (${facets.data.archived})`
    : typeof tree.data?.counts?.archived === 'number' ? ` (${tree.data.counts.archived})` : '')
  // A native <select> sizes its CLOSED box to its WIDEST option text, not
  // just the selected one — a single long tag name or project name was
  // enough to balloon one select past the 390px viewport on its own,
  // wrapping the other two onto their own rows below it. w-auto/min-w-0
  // alone don't cap that; max-w + truncation do (the full text is still
  // there in the opened dropdown, only the closed box's LABEL truncates).
  const REFINE_SELECT_CLS = 'w-auto min-w-0 max-w-[42vw] flex-none text-sm overflow-hidden text-ellipsis whitespace-nowrap'
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
      <div className="mb-4 flex flex-wrap gap-1.5 lg:hidden">
        <Select value={areaSel ?? ''} onChange={e => pick('area', e.target.value || null)} className={REFINE_SELECT_CLS}>
          <option value="">All areas</option>
          {(areas.data?.areas ?? []).map(a => {
            // Navigation, not a filter — every area stays listed even at 0.
            // The count (from the SAME scoped facets the refine row uses)
            // is appended where available so the owner sees what a jump
            // would land on before tapping it.
            const fc = facets.data?.areas.find(f => f.id === a.id)?.count
            return <option key={a.id} value={a.id}>{a.parent_id ? '· ' : ''}{a.name}{typeof fc === 'number' ? ` (${fc})` : ''}</option>
          })}
        </Select>
        <Select value={typeSel ?? ''} onChange={e => pick('type', e.target.value || null)} className={REFINE_SELECT_CLS}>
          <option value="">All types</option>
          {['note', 'playbook', 'wiki'].map(t => <option key={t}>{t}</option>)}
        </Select>
        <Select value={view ?? ''} onChange={e => pick('view', e.target.value || null)} className={REFINE_SELECT_CLS}>
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
            {notes.data && <span className="font-mono text-[10px] text-muted">{rows.length < total ? `${rows.length} of ${total}` : total} note{total === 1 ? '' : 's'}</span>}
          </div>
          {showArchRefine && (
            // Compact on mobile: content-sized selects (w-auto/min-w-0) with
            // a small gap so two fit per row at 390px instead of each one
            // wrapping onto its own row and pushing the first note card off
            // screen — three full-width selects used to cost ~950px of
            // chrome before a single result was visible.
            <div className="mb-3 flex flex-wrap items-center gap-1.5">
              <Select value={showArch ? '1' : ''} onChange={e => refine('arch', e.target.value || null)}
                      aria-label="Archive filter" className={REFINE_SELECT_CLS}>
                <option value="">{activeLabel}</option>
                <option value="1">{archiveLabel}</option>
              </Select>
              {!facets.data ? (
                // Disabled placeholder only on the TRUE first load (no data
                // at all yet) — placeholderData on useNoteFacets carries the
                // previous result through every later filter change, so the
                // select never flashes back to empty/disabled on a tap.
                <Select disabled aria-label="Tag filter" className={REFINE_SELECT_CLS}><option>Loading tags…</option></Select>
              ) : (
                <Select value={tagSel ?? ''} onChange={e => refine('tag', e.target.value || null)}
                        aria-label="Tag filter" className={REFINE_SELECT_CLS}>
                  <option value="">All tags</option>
                  {tagOptions.map(t => <option key={t.tag} value={t.tag}>{t.tag} ({t.count})</option>)}
                </Select>
              )}
              {areaSel && (
                !facets.data ? (
                  <Select disabled aria-label="Project filter" className={REFINE_SELECT_CLS}><option>Loading projects…</option></Select>
                ) : (
                  <Select value={projSel ?? ''} onChange={e => refine('project', e.target.value || null)}
                          aria-label="Project filter" className={REFINE_SELECT_CLS}>
                    <option value="">All projects</option>
                    {projOptions.map(p => <option key={p.id} value={p.id}>{p.name} ({p.count})</option>)}
                  </Select>
                )
              )}
            </div>
          )}
          {notes.isLoading && <Loading rows={5} />}
          {notes.isError && <Empty error title="Could not load notes" note={String(notes.error)} />}
          {notes.data && rows.length === 0 && <Empty title={q ? 'No matches' : 'Nothing here yet'} note={q ? 'Try different words — search covers titles, bodies, entries and tags.' : 'Capture from Home, then file notes here.'} />}
          <div className="flex flex-col gap-2">
            {rows.map(n => <NoteRow key={n.id} n={n} areas={areas.data?.areas} />)}
          </div>
          {notes.hasNextPage && (
            <div className="mt-3 flex justify-center">
              <Btn kind="ghost" busy={notes.isFetchingNextPage} onClick={() => notes.fetchNextPage()}>
                Load more · {rows.length} of {total}
              </Btn>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
