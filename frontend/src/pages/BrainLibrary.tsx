/** Library: folder tree (areas → sub-areas, projects, types, archive) +
 * global search over every note (FTS). Selection lives in the URL. */
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import clsx from 'clsx'
import { useAreas, useNotes, useNotesTree } from '../api'
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
  const q = sp.get('q') ?? ''
  const [draft, setDraft] = useState(q)
  const { inbox } = useBrainCounts()
  const tree = useNotesTree()
  const areas = useAreas()
  const pick = (k: 'area' | 'project' | 'type' | 'view', v: string | null) => {
    const n = new URLSearchParams()
    if (v) n.set(k, v)
    if (q) n.set('q', q)
    setSp(n, { replace: true })
  }
  const filters = q
    ? { q, limit: 100 }
    : view === 'archived' ? { status: 'archived', limit: 100 }
    : { status: undefined as string | undefined, area_id: areaSel ? Number(areaSel) : undefined, project_id: projSel ? Number(projSel) : undefined, type: typeSel ?? undefined, limit: 100 }
  const notes = useNotes(filters)
  const roots = (tree.data?.areas ?? []).filter(a => !a.parent_id)
  const children = (id: number) => (tree.data?.areas ?? []).filter(a => a.parent_id === id)
  const selLabel = q ? `“${q}”` : view === 'archived' ? 'Archive' : typeSel ? typeSel + 's' : projSel ? tree.data?.projects.find(p => String(p.id) === projSel)?.name ?? 'Project' : areaSel ? (tree.data?.areas ?? []).find(a => String(a.id) === areaSel)?.name ?? 'Area' : 'All notes'
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="second-brain // library" title="Library" right={
        <div className="flex flex-wrap items-center gap-2">
          <BrainSubNav inbox={inbox} />
          <form onSubmit={e => { e.preventDefault(); const n = new URLSearchParams(sp); draft ? n.set('q', draft) : n.delete('q'); setSp(n, { replace: true }) }}>
            <Input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Search everything…" className="w-full sm:w-56" />
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
        <Select value={view === 'archived' ? 'archived' : ''} onChange={e => pick('view', e.target.value || null)}>
          <option value="">Active</option><option value="archived">Archive</option>
        </Select>
      </div>

      <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="hidden lg:block">
          <div className="glass rounded-xl p-2">
            {tree.isLoading && <Loading rows={4} />}
            <TreeRow label="All notes" active={!areaSel && !projSel && !typeSel && !view && !q} onClick={() => pick('area', null)} />
            <TreeRow label="Inbox" count={tree.data?.counts?.inbox} active={false} onClick={() => { window.location.href = '/brain' }} />
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
            <TreeRow label="Archive" count={tree.data?.counts?.archived ?? 0} depth={1} active={view === 'archived'} onClick={() => pick('view', 'archived')} />
          </div>
        </div>

        <div className="min-w-0">
          <div className="mb-2 flex items-center justify-between">
            <Label>{selLabel}</Label>
            {notes.data && <span className="font-mono text-[10px] text-muted">{notes.data.notes.length} note{notes.data.notes.length === 1 ? '' : 's'}</span>}
          </div>
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
