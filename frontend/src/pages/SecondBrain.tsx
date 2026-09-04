/** Second Brain Home: capture-first, inbox to triage, recents + area summary.
 * Phase 1 = manual filing; the librarian's proposals arrive in Phase 2. */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAreas, useNotes, type Note } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { BrainSubNav, CaptureBox, FileNoteModal, NoteRow, useBrainCounts } from '../components/brain'
import { Empty, Label, Loading } from '../components/ui'
import { usePageTitle } from '../usePageTitle'

export function SecondBrain() {
  usePageTitle('Second Brain')
  const { tree, inbox } = useBrainCounts()
  const areas = useAreas()
  const inboxNotes = useNotes({ status: 'inbox', limit: 30 })
  const recent = useNotes({ status: 'active', limit: 8 })
  const [filing, setFiling] = useState<Note | null>(null)
  const rootAreas = (tree.data?.areas ?? []).filter(a => !a.parent_id && (a.note_count ?? 0) > 0)
  const maxCount = Math.max(1, ...rootAreas.map(a => a.note_count ?? 0))
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      {filing && <FileNoteModal n={filing} onClose={() => setFiling(null)} />}
      <PageHeader crumb="second-brain" title="Second Brain" right={<BrainSubNav inbox={inbox} />} />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="flex min-w-0 flex-col gap-4">
          <CaptureBox />
          <div className="flex items-center justify-between">
            <Label>Inbox — {inbox} unfiled</Label>
            <Link to="/brain/library" className="text-xs text-accent-2 hover:underline">Open library →</Link>
          </div>
          {inboxNotes.isLoading && <Loading rows={3} />}
          {inboxNotes.data && inboxNotes.data.notes.length === 0 && <Empty title="Inbox zero" note="Everything captured is filed. Nice." />}
          <div className="flex flex-col gap-2">
            {(inboxNotes.data?.notes ?? []).map(n => (
              <div key={n.id} className="glass rounded-xl px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Link to={`/brain/note/${n.id}`} className="min-w-0 flex-1 truncate text-sm font-medium hover:text-accent-2">{n.title}</Link>
                  <span className="font-mono text-[10px] text-muted">{new Date((n.created_at) * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                </div>
                {n.body && <p className="mt-1 line-clamp-2 text-xs text-muted">{n.body}</p>}
                <div className="mt-2 flex gap-2">
                  <button onClick={() => setFiling(n)} className="rounded-full border border-working/60 px-3 py-1 font-mono text-[10px] uppercase tracking-wider text-working hover:bg-working/10">File…</button>
                  <Link to={`/brain/note/${n.id}`} className="rounded-full border border-line px-3 py-1 font-mono text-[10px] uppercase tracking-wider text-muted hover:text-fg">Open</Link>
                </div>
              </div>
            ))}
          </div>
          {recent.data && recent.data.notes.length > 0 && (
            <>
              <Label>Recent notes</Label>
              <div className="flex flex-col gap-2">
                {recent.data.notes.map(n => <NoteRow key={n.id} n={n} areas={areas.data?.areas} showBody={false} />)}
              </div>
            </>
          )}
        </div>
        <div className="flex flex-col gap-4">
          <GlassCard>
            <Label>Areas</Label>
            <div className="mt-3 flex flex-col gap-2 text-sm">
              {tree.isLoading && <Loading rows={3} />}
              {rootAreas.length === 0 && !tree.isLoading && <p className="text-xs text-muted">Notes get areas when you file them.</p>}
              {rootAreas.map(a => (
                <Link key={a.id} to={`/brain/library?area=${a.id}`} className="flex items-center gap-2 hover:text-accent-2">
                  <span className="min-w-0 flex-1 truncate">{a.name}</span>
                  <span className="h-1 w-20 overflow-hidden rounded-full bg-inset"><span className="block h-full rounded-full bg-accent" style={{ width: `${Math.round(100 * (a.note_count ?? 0) / maxCount)}%` }} /></span>
                  <span className="w-6 text-right font-mono text-[11px] text-muted">{a.note_count}</span>
                </Link>
              ))}
            </div>
          </GlassCard>
          <GlassCard accent="var(--hq-accent)">
            <Label>Library</Label>
            <div className="mt-2 grid grid-cols-2 gap-2 text-center">
              {([['note', 'notes'], ['playbook', 'playbooks'], ['wiki', 'wiki'], ['archived', 'archived']] as const).map(([k, label]) => (
                <Link key={k} to={k === 'archived' ? '/brain/library?view=archived' : `/brain/library?type=${k}`} className="rounded-lg border border-line-subtle bg-inset px-2 py-2 hover:border-line">
                  <p className="font-mono text-lg font-semibold">{tree.data?.counts?.[k] ?? 0}</p>
                  <Label>{label}</Label>
                </Link>
              ))}
            </div>
          </GlassCard>
          <GlassCard>
            <Label>Librarian</Label>
            <p className="mt-2 text-xs text-muted">Arrives in Phase 2: files your captures, splits batch dumps, and proposes wiki updates — you approve everything.</p>
          </GlassCard>
        </div>
      </div>
    </section>
  )
}
