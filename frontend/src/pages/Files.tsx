import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import CodeMirror from '@uiw/react-codemirror'
import { EditorView, keymap } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting, type LanguageSupport } from '@codemirror/language'
import { languages } from '@codemirror/language-data'
import { tags as t } from '@lezer/highlight'
import { ApiError, get, getCsrf, post } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Skeleton, Chip } from '../components/ui'
import { Modal, Field, TextInput, Btn } from '../components/Modal'
import { Menu, MenuItem } from '../components/Menu'
import { Markdown } from '../components/chat/Markdown'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'

// Group 5 — Project files. Two roots (projects dir, or a project's primary_path); contract in kis/knowledge/technical.md → Files API.
type Root = { root: string; label: string; path: string; exists: boolean; slug?: string; archived?: boolean }
type Entry = { name: string; is_dir: boolean; size: number | null; mtime: number; symlink?: boolean; hidden?: boolean; outside?: boolean }
type Listing = { root: string; path: string; entries: Entry[]; truncated: boolean }
type FileRead = { root: string; path: string; name: string; kind: 'text' | 'image' | 'pdf' | 'binary'; size: number; mtime: number; content?: string; too_large?: boolean }
type Artifact = { path: string; name: string; is_dir: boolean; size: number | null; inside_primary: boolean; rel: string | null; task_id: number; task_title: string; agent: string; run_id: number }

const q = (o: Record<string, string | number | undefined>) => Object.entries(o).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
const rawUrl = (root: string, path: string, download = false, preview = false) => `/api/files/raw?${q({ root, path, download: download ? 1 : undefined, preview: preview ? 1 : undefined })}`
const fmtSize = (n: number | null) => n == null ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`
const parent = (p: string) => p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : ''
const base = (p: string) => p.slice(p.lastIndexOf('/') + 1)

// Editor chrome follows the app theme through the --hq-* variables; token colours reuse the status palette.
const cmTheme = EditorView.theme({
  '&': { backgroundColor: 'transparent', color: 'var(--hq-text)', fontSize: '13px', height: '100%' },
  '.cm-content': { fontFamily: 'var(--hq-font-mono)', caretColor: 'var(--hq-accent-2)', padding: '8px 0' },
  '.cm-scroller': { fontFamily: 'var(--hq-font-mono)', lineHeight: '1.55' },
  '.cm-gutters': { backgroundColor: 'transparent', color: 'var(--hq-muted)', border: 'none', opacity: '0.7' },
  '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'color-mix(in srgb, var(--hq-accent) 10%, transparent)' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': { backgroundColor: 'color-mix(in srgb, var(--hq-accent) 30%, transparent) !important' },
  '.cm-cursor': { borderLeftColor: 'var(--hq-accent-2)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-matchingBracket': { backgroundColor: 'color-mix(in srgb, var(--hq-accent-2) 25%, transparent)' },
})
const cmHighlight = syntaxHighlighting(HighlightStyle.define([
  { tag: [t.keyword, t.modifier, t.operatorKeyword], color: 'var(--hq-accent)' },
  { tag: [t.string, t.special(t.string)], color: 'var(--hq-working)' },
  { tag: [t.number, t.bool, t.null, t.atom], color: 'var(--hq-needsyou)' },
  { tag: [t.comment, t.meta], color: 'var(--hq-muted)', fontStyle: 'italic' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.variableName)], color: 'var(--hq-accent-2)' },
  { tag: [t.typeName, t.className, t.tagName], color: 'var(--hq-queued)' },
  { tag: [t.propertyName, t.attributeName], color: 'var(--hq-done)' },
  { tag: t.heading, fontWeight: '600', color: 'var(--hq-accent-2)' },
  { tag: [t.link, t.url], color: 'var(--hq-accent-2)', textDecoration: 'underline' },
  { tag: t.emphasis, fontStyle: 'italic' }, { tag: t.strong, fontWeight: '600' },
  { tag: t.invalid, color: 'var(--hq-error)' },
]))

/** Lazy grammar for the open file's name (language-data loads each grammar on demand). */
function useLanguage(name: string) {
  const [lang, setLang] = useState<LanguageSupport | null>(null)
  useEffect(() => {
    let alive = true
    const d = languages.find(l => l.extensions.some(e => name.toLowerCase().endsWith('.' + e)) || l.filename?.test(name))
    if (!d) { setLang(null); return }
    d.load().then(l => { if (alive) setLang(l) })
    return () => { alive = false }
  }, [name])
  return lang
}

export function Files() {
  usePageTitle('Files')
  const [sp, setSp] = useSearchParams()
  const root = sp.get('root') || 'projects'
  const file = sp.get('path') || ''
  const dir = sp.get('dir') || (file ? parent(file) : '')
  const [hidden, setHidden] = useState(false)
  const [sheet, setSheet] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()
  const roots = useQuery({ queryKey: ['files-roots'], queryFn: () => get<{ roots: Root[] }>('/api/files/roots') })
  const current = roots.data?.roots.find(r => r.root === root)
  const setParams = useCallback((p: { root?: string; path?: string; dir?: string }) => {
    setSp(prev => { const n = new URLSearchParams(prev); for (const [k, v] of Object.entries(p)) { if (v) n.set(k, v); else n.delete(k) } return n })
  }, [setSp])
  const open = useCallback((path: string) => { setParams({ path, dir: parent(path) }); setSheet(false) }, [setParams])
  const refresh = useCallback((d: string) => { qc.invalidateQueries({ queryKey: ['files-list', root, d] }) }, [qc, root])

  // ---- mutations (small enough to live here) ----
  type ModalState = null | { kind: 'newfile' | 'newdir'; dir: string } | { kind: 'rename'; path: string } | { kind: 'delete'; path: string; is_dir: boolean }
  const [modal, setModalRaw] = useState<ModalState>(null)
  const setModal = useCallback((m: ModalState) => { setModalRaw(m); if (m) setSheet(false) }, [])   // the phone sheet and modals share a layer: never stack them
  const [busy, setBusy] = useState(false)
  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true)
    try { await fn(); if (ok) toast(ok); setModal(null) } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  const upload = async (d: string, list: FileList | null) => {
    if (!list?.length) return
    const form = new FormData(); form.set('root', root); form.set('path', d)
    for (const f of Array.from(list)) form.append('files', f)
    setBusy(true)
    try {
      const r = await fetch('/api/files/upload', { method: 'POST', headers: { 'x-csrf': getCsrf() }, body: form })
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(typeof j.detail === 'string' ? j.detail : j.detail?.error ?? `${r.status}`) }
      const j = await r.json() as { files: { name: string }[] }
      toast(`Uploaded ${j.files.length} file${j.files.length === 1 ? '' : 's'}`); refresh(d)
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err'); refresh(d) } finally { setBusy(false) }
  }
  const uploadRef = useRef<HTMLInputElement>(null)
  const [uploadDir, setUploadDir] = useState('')

  const tree = (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <select value={root} onChange={e => { setParams({ root: e.target.value, path: '', dir: '' }) }} aria-label="Root"
          className="hq-select min-w-0 flex-1 appearance-none truncate rounded-lg border border-line bg-inset py-1.5 pl-2 pr-8 text-sm outline-none focus:border-accent">
          {roots.data?.roots.map(r => <option key={r.root} value={r.root} disabled={!r.exists}>{r.label}{r.archived ? ' (archived)' : ''}{r.exists ? '' : ' — missing'}</option>) ?? <option value={root}>{root}</option>}
        </select>
        <Menu button={<span>＋</span>}>
          <MenuItem onClick={() => setModal({ kind: 'newfile', dir })}>New file</MenuItem>
          <MenuItem onClick={() => setModal({ kind: 'newdir', dir })}>New folder</MenuItem>
          <MenuItem onClick={() => { setUploadDir(dir); uploadRef.current?.click() }}>Upload…</MenuItem>
          <MenuItem onClick={() => setHidden(h => !h)} active={hidden}>{hidden ? 'Hide' : 'Show'} hidden</MenuItem>
        </Menu>
      </div>
      {current && <p className="mb-2 truncate font-mono text-[10px] text-muted" title={current.path}>{current.path}</p>}
      <input ref={uploadRef} type="file" multiple className="hidden" onChange={e => { upload(uploadDir, e.target.files); e.target.value = '' }} />
      <div className="min-h-0 flex-1 overflow-auto pr-1">
        {current?.slug && <Artifacts slug={current.slug} onOpen={open} />}
        <DirRows root={root} path="" hidden={hidden} depth={0} selected={file} openDir={dir} onOpen={open}
          onMenu={(e, p) => setModal(e.is_dir ? { kind: 'delete', path: p, is_dir: true } : { kind: 'rename', path: p })}
          menu={(e, p) => (
            <Menu button={<span>⋯</span>}>
              {e.is_dir && <MenuItem onClick={() => setModal({ kind: 'newfile', dir: p })}>New file here</MenuItem>}
              {e.is_dir && <MenuItem onClick={() => setModal({ kind: 'newdir', dir: p })}>New folder here</MenuItem>}
              {e.is_dir && <MenuItem onClick={() => { setUploadDir(p); uploadRef.current?.click() }}>Upload here…</MenuItem>}
              {!e.is_dir && <MenuItem onClick={() => window.open(rawUrl(root, p, true), '_blank')}>Download</MenuItem>}
              <MenuItem onClick={() => setModal({ kind: 'rename', path: p })}>Rename</MenuItem>
              <MenuItem onClick={() => setModal({ kind: 'delete', path: p, is_dir: e.is_dir })}>Delete</MenuItem>
            </Menu>
          )} />
      </div>
    </div>
  )

  return (
    <section className="mx-auto max-w-7xl p-4 sm:p-6">
      <PageHeader crumb="files" title="Files" right={
        <div className="flex items-center gap-2">
          <Btn kind="ghost" className="lg:hidden" onClick={() => setSheet(true)} aria-label="Browse files"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10" /></svg>Browse</Btn>
        </div>} />
      {modal?.kind === 'newfile' && <NameModal title="New file" label="File name" hint={`in ${current?.label ?? root}/${modal.dir}`} busy={busy} onClose={() => setModal(null)}
        onSubmit={n => act(async () => { const p = modal.dir ? `${modal.dir}/${n}` : n; await post('/api/files/write', { root, path: p, content: '' }); refresh(modal.dir); open(p) })} />}
      {modal?.kind === 'newdir' && <NameModal title="New folder" label="Folder name" hint={`in ${current?.label ?? root}/${modal.dir}`} busy={busy} onClose={() => setModal(null)}
        onSubmit={n => act(async () => { await post('/api/files/mkdir', { root, path: modal.dir ? `${modal.dir}/${n}` : n }); refresh(modal.dir) }, 'Folder created')} />}
      {modal?.kind === 'rename' && <NameModal title="Rename" label="New name" initial={base(modal.path)} busy={busy} onClose={() => setModal(null)}
        onSubmit={n => act(async () => {
          const r = await post<{ path: string }>('/api/files/rename', { root, path: modal.path, to: n }); refresh(parent(modal.path))
          if (file === modal.path) open(r.path); else if (file.startsWith(modal.path + '/')) open(r.path + file.slice(modal.path.length))
        }, 'Renamed')} />}
      {modal?.kind === 'delete' && (
        <Modal title={`Delete ${modal.is_dir ? 'folder' : 'file'}`} onClose={() => setModal(null)}>
          <p className="text-sm">Delete <span className="font-mono">{modal.path}</span>{modal.is_dir ? ' and everything inside it' : ''}? This cannot be undone.</p>
          <div className="mt-4 flex justify-end gap-2"><Btn kind="ghost" onClick={() => setModal(null)}>Cancel</Btn>
            <Btn kind="warn" busy={busy} onClick={() => act(async () => {
              await post('/api/files/delete', { root, path: modal.path, recursive: modal.is_dir }); refresh(parent(modal.path))
              if (file === modal.path || file.startsWith(modal.path + '/')) setParams({ path: '', dir: parent(modal.path) })
            }, 'Deleted')}>Delete</Btn></div>
        </Modal>)}
      <div className="grid min-w-0 gap-4 lg:grid-cols-[18rem_1fr]">
        <GlassCard className="hidden min-w-0 lg:flex lg:h-[calc(100dvh-12.5rem)] lg:flex-col">{roots.isLoading ? <Skeleton rows={8} /> : tree}</GlassCard>
        {sheet && (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-label="Files" data-sheet>
            <div className="absolute inset-0 bg-bg/60" onClick={() => setSheet(false)} />
            <div className="absolute inset-x-0 bottom-0 flex h-[80dvh] flex-col rounded-t-2xl border border-line hq-menu p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-2xl" style={{ backdropFilter: 'blur(18px)', WebkitBackdropFilter: 'blur(18px)' }}>
              <div className="mx-auto mb-2 h-1 w-10 shrink-0 rounded-full bg-line" />
              {tree}
            </div>
          </div>)}
        <GlassCard className="flex h-[calc(100dvh-15.5rem)] min-h-[16rem] min-w-0 flex-col overflow-hidden sm:h-[calc(100dvh-12.5rem)] hq-editor-card">
          {file
            ? <Editor key={`${root}:${file}`} root={root} path={file} onDeleted={() => setParams({ path: '' })} onCrumb={d => { setParams({ dir: d }); setSheet(true) }} />
            : <Empty title="No file open" note={current ? `Pick a file under ${current.label}.` : 'Pick a root.'} />}
        </GlassCard>
      </div>
    </section>
  )
}

/** One directory level, lazily expanded. The open file's ancestors start expanded. */
function DirRows({ root, path, hidden, depth, selected, openDir, onOpen, menu }: {
  root: string; path: string; hidden: boolean; depth: number; selected: string; openDir: string
  onOpen: (p: string) => void; onMenu?: (e: Entry, p: string) => void; menu: (e: Entry, p: string) => ReactNode
}) {
  const l = useQuery({ queryKey: ['files-list', root, path, hidden], queryFn: () => get<Listing>(`/api/files/list?${q({ root, path, hidden: hidden ? 1 : 0 })}`) })
  const [openDirs, setOpenDirs] = useState<Set<string>>(() => new Set())
  useEffect(() => {   // auto-expand toward the open file / dir
    const target = openDir
    if (!target) return
    setOpenDirs(s => { const n = new Set(s); const segs = target.split('/'); let acc = ''; for (const seg of segs) { acc = acc ? `${acc}/${seg}` : seg; if (acc.startsWith(path ? path + '/' : '') || !path) n.add(acc) } return n })
  }, [openDir, path])
  if (l.isLoading) return <div className="py-1" style={{ paddingLeft: depth * 12 }}><Skeleton rows={depth === 0 ? 6 : 2} /></div>
  if (l.isError) return <p className="px-2 py-1 text-xs text-error" style={{ paddingLeft: 8 + depth * 12 }}>{String((l.error as Error).message)}</p>
  const d = l.data!
  if (!d.entries.length && depth === 0) return <Empty title="Empty folder" note={hidden ? undefined : 'Hidden entries are filtered — use ＋ › Show hidden.'} />
  return (
    <ul className="min-w-0">
      {d.entries.map(e => {
        const p = path ? `${path}/${e.name}` : e.name
        const isOpen = openDirs.has(p)
        const sel = selected === p
        return (
          <li key={e.name} className="min-w-0">
            <div className={clsx('group flex min-w-0 items-center gap-1 rounded-md pr-1 text-[13px] hover:bg-raised', sel && 'bg-raised text-accent-2')} style={{ paddingLeft: 4 + depth * 12 }}>
              <button type="button" onClick={() => e.is_dir ? setOpenDirs(s => { const n = new Set(s); if (n.has(p)) n.delete(p); else n.add(p); return n }) : onOpen(p)}
                className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left" title={e.outside ? 'Symlink outside the root — not followed' : p}>
                <span className="w-3 shrink-0 text-center font-mono text-[10px] text-muted">{e.is_dir ? (isOpen ? '▾' : '▸') : ''}</span>
                <span className={clsx('truncate', e.hidden && 'opacity-60', e.outside && 'line-through opacity-50')}>{e.name}{e.is_dir ? '/' : ''}</span>
                {e.symlink && <span className="shrink-0 font-mono text-[9px] text-muted">→</span>}
                {!e.is_dir && <span className="ml-auto shrink-0 font-mono text-[10px] text-muted opacity-0 group-hover:opacity-100">{fmtSize(e.size)}</span>}
              </button>
              <span className="shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100">{menu(e, p)}</span>
            </div>
            {e.is_dir && isOpen && !e.outside && <DirRows root={root} path={p} hidden={hidden} depth={depth + 1} selected={selected} openDir={openDir} onOpen={onOpen} menu={menu} />}
          </li>)
      })}
      {d.truncated && <li className="px-2 py-1 text-[11px] text-muted">Showing the first {d.entries.length} entries.</li>}
    </ul>
  )
}

function Artifacts({ slug, onOpen }: { slug: string; onOpen: (p: string) => void }) {
  const a = useQuery({ queryKey: ['files-artifacts', slug], queryFn: () => get<{ artifacts: Artifact[] }>(`/api/project/${slug}/artifacts`) })
  const list = a.data?.artifacts ?? []
  const [open, setOpen] = useState<boolean | null>(null)   // long lists start collapsed so the tree stays reachable
  const isOpen = open ?? list.length <= 8
  if (a.isLoading) return <Skeleton rows={2} />
  if (!list.length) return null
  return (
    <div className="mb-2 border-b border-line-subtle pb-2">
      <button type="button" onClick={() => setOpen(!isOpen)} className="flex w-full items-center gap-1 py-1 font-mono text-[10px] uppercase tracking-widest text-muted hover:text-fg"><span className="w-3 text-center">{isOpen ? '▾' : '▸'}</span>Artifacts <Chip tone="muted">{list.length}</Chip></button>
      {isOpen && <ul className="min-w-0">
        {list.map(x => (
          <li key={x.path} className="min-w-0">
            {x.inside_primary && x.rel && !x.is_dir
              ? <button type="button" onClick={() => onOpen(x.rel!)} className="flex w-full min-w-0 items-center gap-1.5 rounded-md py-1 pl-4 pr-1 text-left text-[13px] hover:bg-raised" title={`${x.task_title} · ${x.agent} · run #${x.run_id}`}><span className="truncate">{x.rel}</span></button>
              : <span className="flex min-w-0 items-center gap-1.5 py-1 pl-4 pr-1 text-[13px] text-muted" title={`${x.path} — outside this root (${x.task_title})`}><span className="truncate">{x.name}</span><span className="shrink-0 font-mono text-[9px]">outside</span></span>}
          </li>))}
      </ul>}
    </div>
  )
}

function NameModal({ title, label, hint, initial = '', busy, onClose, onSubmit }: { title: string; label: string; hint?: string; initial?: string; busy: boolean; onClose: () => void; onSubmit: (name: string) => void }) {
  const [v, setV] = useState(initial)
  const ok = v.trim() && !v.includes('/') && v !== '.' && v !== '..'
  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={e => { e.preventDefault(); if (ok) onSubmit(v.trim()) }} className="space-y-3">
        <Field label={label} hint={hint}><TextInput autoFocus value={v} onChange={e => setV(e.target.value)} onFocus={e => { const i = e.target.value.lastIndexOf('.'); e.target.setSelectionRange(0, i > 0 ? i : e.target.value.length) }} /></Field>
        <div className="flex justify-end gap-2"><Btn kind="ghost" type="button" onClick={onClose}>Cancel</Btn><Btn type="submit" busy={busy} disabled={!ok}>{title}</Btn></div>
      </form>
    </Modal>
  )
}

/** Editor / preview for one file. Keeps its own text + the mtime it loaded; a 409 on save opens the Reload/Overwrite choice. */
function Editor({ root, path, onDeleted, onCrumb }: { root: string; path: string; onDeleted: () => void; onCrumb: (dir: string) => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const f = useQuery({ queryKey: ['files-read', root, path], queryFn: () => get<FileRead>(`/api/files/read?${q({ root, path })}`), retry: false })
  const [text, setText] = useState<string | null>(null)
  const [loadedMtime, setLoadedMtime] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const [preview, setPreview] = useState(false)
  const lang = useLanguage(base(path))
  useEffect(() => { if (f.data?.kind === 'text' && f.data.content !== undefined && loadedMtime === null) { setText(f.data.content); setLoadedMtime(f.data.mtime) } }, [f.data, loadedMtime])
  const dirty = text !== null && f.data?.content !== undefined && text !== f.data.content
  const isMd = /\.(md|markdown)$/i.test(path)
  const isHtml = /\.(html?|xhtml)$/i.test(path)

  const save = useCallback(async (force = false) => {
    if (text === null || saving) return
    setSaving(true)
    try {
      const r = await post<{ mtime: number }>('/api/files/write', { root, path, content: text, mtime: loadedMtime, force })
      setLoadedMtime(r.mtime); setConflict(null)
      qc.setQueryData(['files-read', root, path], (old: FileRead | undefined) => old ? { ...old, content: text, mtime: r.mtime, size: new TextEncoder().encode(text).length } : old)
      qc.invalidateQueries({ queryKey: ['files-list', root, parent(path)] })
      toast('Saved')
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict(e.message)
      else toast(e instanceof Error ? e.message : String(e), 'err')
    } finally { setSaving(false) }
  }, [text, saving, root, path, loadedMtime, qc, toast])
  const reload = async () => {   // seed from the fresh response, not the cached one
    setConflict(null)
    const r = await f.refetch()
    if (r.data?.kind === 'text' && r.data.content !== undefined) { setText(r.data.content); setLoadedMtime(r.data.mtime) }
  }
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (dirty) void save() } }
    document.addEventListener('keydown', k); return () => document.removeEventListener('keydown', k)
  }, [dirty, save])
  useEffect(() => {
    if (!dirty) return
    const h = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', h); return () => window.removeEventListener('beforeunload', h)
  }, [dirty])
  const extensions = useMemo(() => [cmTheme, cmHighlight, EditorView.lineWrapping, keymap.of([{ key: 'Mod-s', run: () => true }]), ...(lang ? [lang] : [])], [lang])

  const crumbs = path.split('/')
  const header = (
    <div className="mb-2 flex min-w-0 items-center gap-2 text-xs">
      <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden font-mono text-[11px] text-muted" aria-label="Path">
        {crumbs.map((c, i) => {
          const d = crumbs.slice(0, i).join('/')
          const last = i === crumbs.length - 1
          return <span key={i} className={clsx('flex min-w-0 items-center gap-1', last && 'min-w-0 flex-1')}>{i > 0 && <span className="opacity-50">/</span>}
            {last ? <span className="truncate font-medium text-fg">{c}</span> : <button type="button" onClick={() => onCrumb(d ? `${d}/${c}` : c)} className="hidden truncate hover:text-fg sm:inline">{c}</button>}</span>
        })}
      </nav>
      {f.data && <span className="hidden shrink-0 font-mono text-[10px] text-muted sm:inline">{fmtSize(f.data.size)}</span>}
      {dirty && <Chip>unsaved</Chip>}
      {(isMd || isHtml) && f.data?.kind === 'text' && <Btn kind="ghost" onClick={() => setPreview(p => !p)}>{preview ? 'Edit' : 'Preview'}</Btn>}
      {f.data && f.data.kind !== 'text' && <a href={rawUrl(root, path, true)} className="rounded-full border border-line px-3 py-1 font-mono text-[11px] uppercase tracking-wider text-muted hover:text-fg">Download</a>}
      {f.data?.kind === 'text' && !f.data.too_large && <Btn busy={saving} disabled={!dirty} onClick={() => save()}>Save</Btn>}
    </div>
  )
  if (f.isLoading) return <>{header}<Skeleton rows={10} /></>
  if (f.isError) {
    const err = f.error as ApiError
    if (err.status === 404) { return <>{header}<Empty error title="File not found" note="It may have been moved or deleted." /><div className="mt-3"><Btn kind="ghost" onClick={onDeleted}>Close</Btn></div></> }
    return <>{header}<Empty error title="Could not open" note={err.message} /></>
  }
  const d = f.data!
  let body: ReactNode
  if (d.kind === 'image') body = <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto rounded-lg bg-inset p-3"><img src={rawUrl(root, path)} alt={d.name} className="max-h-full max-w-full object-contain" /></div>
  else if (d.kind === 'pdf') body = <Empty title={d.name} note={`PDF · ${fmtSize(d.size)}`} />
  else if (d.kind === 'binary' || d.too_large) body = <Empty title={d.name} note={d.too_large ? `Text files over 1 MB open read-only via Download (${fmtSize(d.size)}).` : `Binary file · ${fmtSize(d.size)}`} />
  else if (preview && isHtml) body = (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-line-subtle bg-white">
      {dirty && <p className="bg-inset px-3 py-1 font-mono text-[10px] text-muted">Preview shows the saved file — save to refresh.</p>}
      <iframe key={loadedMtime ?? 0} title={d.name} sandbox="allow-scripts" src={rawUrl(root, path, false, true)} className="min-h-0 flex-1 w-full" />
    </div>)
  else if (preview) body = <div className="min-h-0 flex-1 overflow-auto rounded-lg bg-inset p-4 text-sm"><Markdown text={text ?? ''} /></div>
  else body = (
    <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-line-subtle bg-inset" data-editor>
      <CodeMirror value={text ?? ''} height="100%" className="h-full" extensions={extensions} onChange={v => setText(v)} basicSetup={{ foldGutter: false, highlightActiveLine: true, autocompletion: false }} theme="none" />
    </div>)
  return (
    <>
      {header}
      {d.kind === 'pdf' && <div className="mb-2"><a href={rawUrl(root, path)} target="_blank" rel="noreferrer" className="rounded-full border border-line px-3 py-1 font-mono text-[11px] uppercase tracking-wider text-muted hover:text-fg">Open PDF</a></div>}
      {body}
      {conflict && (
        <Modal title="File changed on disk" onClose={() => setConflict(null)}>
          <p className="text-sm">{conflict === 'changed on disk' ? 'Someone (probably an agent) wrote this file after you opened it.' : conflict === 'deleted on disk' ? 'The file was deleted after you opened it.' : conflict} Reload to see their version and lose your edits, or overwrite with yours.</p>
          <div className="mt-4 flex justify-end gap-2"><Btn kind="ghost" onClick={reload}>Reload</Btn><Btn kind="warn" busy={saving} onClick={() => save(true)}>Overwrite</Btn></div>
        </Modal>)}
    </>
  )
}
