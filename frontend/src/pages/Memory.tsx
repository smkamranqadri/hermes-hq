import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import CodeMirror from '@uiw/react-codemirror'
import { EditorView, keymap } from '@codemirror/view'
import { EditorSelection } from '@codemirror/state'
import { cmTheme, cmHighlight, useLanguage } from '../components/editor'
import { ApiError, get, post } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Skeleton, Chip, Spinner } from '../components/ui'
import { Modal, Btn } from '../components/Modal'
import { Menu, MenuItem } from '../components/Menu'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'
import { AgentSwitcher } from '../components/AgentSwitcher'

// Group 6-2 — Memory browser: built-in memory files per agent, Hermes memory providers, learning graph.
type MemFile = { name: string; size: number; mtime: number | null; chars: number | null; entries: number | null; limit: number | null; kind: 'memory' | 'user' | 'other'; missing?: boolean }
type Files = { profile: string; dir: string; files: MemFile[]; limits: { memory: number; user: number } }
type Read = { name: string; content: string; mtime: number | null; size: number; missing?: boolean }
type Hit = { profile: string; name: string; line: number; text: string }
type Field = { key: string; label: string; kind: 'text' | 'secret' | 'select' | 'boolean' | 'integer' | 'number'; description: string; placeholder: string; required: boolean; value: unknown; is_set: boolean; options: { value: string; label: string }[]; url: string; when: Record<string, unknown> | null; minimum?: number | null; maximum?: number | null; step?: number | null }
type Provider = { name: string; label: string; description: string; status: 'ready' | 'needs_config' | 'unavailable' | 'missing'; available: boolean; configured: boolean; fields: Field[]; setup: { pip_dependencies: string[]; external_dependencies: { name: string; install: string; check: string }[]; required_env: string[]; dependencies_installed: boolean } }
type Providers = { active: string; providers: Provider[] }
type GNode = { id: string; label: string; kind: 'skill' | 'memory'; category: string; useCount: number; state: string; createdBy: string; pinned: boolean; timestamp: number | null; memorySource?: string }
type Graph = { nodes: GNode[]; edges: { source: string; target: string }[]; clusters: { category: string; count: number }[]; memory: { source: string; title: string; body: string }[]; stats: Record<string, unknown> }
type Job = { id: string; status: 'running' | 'done' | 'failed'; log: string; result: { ok?: boolean; results?: { name?: string; status: string; output?: string }[]; error?: string } | null }

const q = (o: Record<string, string | number | undefined>) => Object.entries(o).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
const fmtSize = (n: number) => n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`
const ago = (t: number | null) => t == null ? '—' : new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const TABS = ['files', 'providers', 'graph'] as const
type Tab = (typeof TABS)[number]

export function Memory() {
  usePageTitle('Memory')
  const [params, setParamsRaw] = useSearchParams()
  const profile = params.get('profile') || 'orchestrator'
  const tab = (TABS as readonly string[]).includes(params.get('tab') || '') ? (params.get('tab') as Tab) : 'files'
  const file = params.get('file') || ''
  const line = Number(params.get('line') || 0) || 0
  const setParams = useCallback((patch: Record<string, string>) => setParamsRaw(p => { const n = new URLSearchParams(p); for (const [k, v] of Object.entries(patch)) { if (v) n.set(k, v); else n.delete(k) } return n }, { replace: true }), [setParamsRaw])
  const [sheet, setSheet] = useState(false)

  return (
    <section className="mx-auto max-w-7xl p-4 sm:p-6">
      <PageHeader crumb="memory" title="Memory" right={
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <AgentSwitcher value={profile} onChange={p => setParams({ profile: p, file: '', line: '' })} className="w-full sm:w-40" />
          <div role="tablist" className="flex rounded-full border border-line bg-glass p-0.5 font-mono text-[10px] uppercase tracking-wider">
            {TABS.map(t => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setParams({ tab: t })} className={clsx('rounded-full px-2.5 py-1', tab === t ? 'bg-accent/20 text-fg' : 'text-muted hover:text-fg')}>{t}</button>)}
          </div>
          {tab === 'files' && <Btn kind="ghost" className="lg:hidden" onClick={() => setSheet(true)} aria-label="Browse memory files">Browse</Btn>}
        </div>} />
      {tab === 'files' && <FilesTab profile={profile} file={file} line={line} setParams={setParams} sheet={sheet} setSheet={setSheet} />}
      {tab === 'providers' && <ProvidersTab profile={profile} />}
      {tab === 'graph' && <GraphTab profile={profile} setParams={setParams} />}
    </section>
  )
}

// ---------------------------------------------------------------- Files
function FilesTab({ profile, file, line, setParams, sheet, setSheet }: { profile: string; file: string; line: number; setParams: (p: Record<string, string>) => void; sheet: boolean; setSheet: (b: boolean) => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const files = useQuery({ queryKey: ['memory-files', profile], queryFn: () => get<Files>(`/api/memory/files?${q({ profile })}`) })
  const [search, setSearch] = useState('')
  const hits = useQuery({ queryKey: ['memory-search', search], queryFn: () => get<{ hits: Hit[]; truncated: boolean }>(`/api/memory/search?${q({ q: search })}`), enabled: search.trim().length > 0 })
  const [resetTarget, setResetTarget] = useState<MemFile | null>(null)
  useEffect(() => { if (!file && files.data?.files.length) setParams({ file: files.data.files[0].name }) }, [file, files.data, setParams])
  const open = (name: string, ln?: number, prof?: string) => { setParams({ profile: prof ?? profile, file: name, line: ln ? String(ln) : '' }); setSheet(false) }

  const list = (
    <div className="flex min-h-0 flex-1 flex-col">
      <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search all agents' memory" aria-label="Search memory"
        className="mb-2 w-full rounded-lg border border-line bg-inset px-3 py-1.5 text-sm outline-none placeholder:text-muted focus:border-accent" />
      {search.trim() ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {hits.isLoading ? <Skeleton rows={4} /> : !hits.data?.hits.length ? <Empty title="No matches" /> : (
            <>{hits.data.hits.map((h, i) => (
              <button key={i} onClick={() => open(h.name, h.line, h.profile)} className="block w-full rounded-lg px-2 py-1.5 text-left hover:bg-accent/10">
                <p className="font-mono text-[10px] text-muted">{h.profile} · {h.name}:{h.line}</p>
                <p className="truncate text-xs">{h.text}</p>
              </button>))}
              {hits.data.truncated && <p className="p-2 font-mono text-[10px] text-muted">first 200 matches shown</p>}</>)}
        </div>
      ) : files.isLoading ? <Skeleton rows={4} /> : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {files.data?.files.map(f => (
            <div key={f.name} className={clsx('group flex items-center gap-2 rounded-lg px-2 py-1.5', f.name === file ? 'bg-accent/15' : 'hover:bg-accent/10')}>
              <button onClick={() => open(f.name)} className="min-w-0 flex-1 text-left">
                <p className="flex items-center gap-2 text-sm"><span className="truncate font-medium">{f.name}</span>{f.missing && <Chip tone="muted">empty</Chip>}{f.entries != null && f.entries > 0 && <Chip tone="muted">{f.entries} §</Chip>}</p>
                <p className="font-mono text-[10px] text-muted">{f.missing ? 'not created yet' : `${fmtSize(f.size)} · ${ago(f.mtime)}`}</p>
                {f.limit != null && f.chars != null && <Meter value={f.chars} max={f.limit} />}
              </button>
              {!f.missing && f.kind !== 'other' && <Menu button={<span>⋯</span>}><MenuItem onClick={() => setResetTarget(f)}>Reset (delete)</MenuItem></Menu>}
            </div>))}
          {files.data && <p className="mt-2 truncate px-2 font-mono text-[10px] text-muted" title={files.data.dir}>{files.data.dir}</p>}
        </div>)}
    </div>
  )

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[18rem_1fr]">
      {resetTarget && (
        <Modal title={`Reset ${resetTarget.name}`} onClose={() => setResetTarget(null)}>
          <p className="text-sm">Delete <span className="font-mono">{resetTarget.name}</span> for <b>{profile}</b>? The agent starts with an empty file. This cannot be undone.</p>
          <div className="mt-4 flex justify-end gap-2"><Btn kind="ghost" onClick={() => setResetTarget(null)}>Cancel</Btn>
            <Btn kind="warn" onClick={async () => { try { await post('/api/memory/reset', { profile, target: resetTarget.kind }); toast(`${resetTarget.name} deleted`); qc.invalidateQueries({ queryKey: ['memory-files', profile] }); qc.invalidateQueries({ queryKey: ['memory-read', profile, resetTarget.name] }) } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } setResetTarget(null) }}>Delete</Btn></div>
        </Modal>)}
      <GlassCard className="hidden min-w-0 lg:flex lg:h-[calc(100dvh-12.5rem)] lg:flex-col">{list}</GlassCard>
      {sheet && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-label="Memory files" data-sheet>
          <div className="absolute inset-0 bg-bg/60" onClick={() => setSheet(false)} />
          <div className="absolute inset-x-0 bottom-0 flex h-[80dvh] flex-col rounded-t-2xl border border-line hq-menu p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-2xl">
            <div className="mx-auto mb-2 h-1 w-10 shrink-0 rounded-full bg-line" />{list}
          </div>
        </div>)}
      <GlassCard className="flex h-[calc(100dvh-15.5rem)] min-h-[16rem] min-w-0 flex-col overflow-hidden sm:h-[calc(100dvh-12.5rem)] hq-editor-card">
        {file ? <Editor key={`${profile}:${file}`} profile={profile} name={file} line={line} limit={files.data?.files.find(f => f.name === file)?.limit ?? null} /> : <Empty title="No file open" note="Pick a memory file." />}
      </GlassCard>
    </div>
  )
}

function Meter({ value, max }: { value: number; max: number }) {
  const pct = Math.min(100, Math.round(100 * value / Math.max(1, max)))
  return <span className="mt-1 flex items-center gap-1.5" title={`${value} / ${max} chars (Hermes limit)`}><span className="h-1 w-24 overflow-hidden rounded-full bg-line"><span className={clsx('block h-full rounded-full', pct >= 95 ? 'bg-error' : pct >= 80 ? 'bg-needsyou' : 'bg-accent')} style={{ width: `${pct}%` }} /></span><span className="font-mono text-[10px] text-muted">{value}/{max}</span></span>
}

function Editor({ profile, name, line, limit }: { profile: string; name: string; line: number; limit: number | null }) {
  const qc = useQueryClient(); const toast = useToast()
  const f = useQuery({ queryKey: ['memory-read', profile, name], queryFn: () => get<Read>(`/api/memory/read?${q({ profile, name })}`), retry: false })
  const [text, setText] = useState<string | null>(null)
  const [loadedMtime, setLoadedMtime] = useState<number | null | undefined>(undefined)
  const [saving, setSaving] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const lang = useLanguage(name)
  const view = useRef<EditorView | null>(null)
  const [ready, setReady] = useState(false)
  useEffect(() => { if (f.data && loadedMtime === undefined) { setText(f.data.content); setLoadedMtime(f.data.mtime) } }, [f.data, loadedMtime])
  useEffect(() => {   // jump to a search hit once the editor holds the document
    if (!ready || !line || text === null) return
    let tries = 0
    const tick = () => {
      const v = view.current; if (!v) return
      if (v.state.doc.length < text.length && tries++ < 20) { setTimeout(tick, 50); return }
      const ln = Math.min(line, v.state.doc.lines); const l = v.state.doc.line(ln)
      v.dispatch({ selection: EditorSelection.range(l.from, l.to), effects: EditorView.scrollIntoView(l.from, { y: 'center' }) }); v.focus()
    }
    const t = setTimeout(tick, 30)
    return () => clearTimeout(t)
  }, [ready, line, text === null]) // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = text !== null && f.data !== undefined && text !== f.data.content
  const save = useCallback(async (force = false) => {
    if (text === null || saving) return
    setSaving(true)
    try {
      const r = await post<{ mtime: number; size: number }>('/api/memory/write', { profile, name, content: text, mtime: loadedMtime, force })
      setLoadedMtime(r.mtime); setConflict(null)
      qc.setQueryData(['memory-read', profile, name], (old: Read | undefined) => old ? { ...old, content: text, mtime: r.mtime, size: r.size, missing: false } : old)
      qc.invalidateQueries({ queryKey: ['memory-files', profile] }); toast('Saved')
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict(e.message)
      else if (e instanceof ApiError && e.status === 423) toast(e.message, 'err')
      else toast(e instanceof Error ? e.message : String(e), 'err')
    } finally { setSaving(false) }
  }, [text, saving, profile, name, loadedMtime, qc, toast])
  const reload = async () => { setConflict(null); const r = await f.refetch(); if (r.data) { setText(r.data.content); setLoadedMtime(r.data.mtime) } }
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (dirty) void save() } }
    document.addEventListener('keydown', k); return () => document.removeEventListener('keydown', k)
  }, [dirty, save])
  useEffect(() => { if (!dirty) return; const h = (e: BeforeUnloadEvent) => { e.preventDefault() }; window.addEventListener('beforeunload', h); return () => window.removeEventListener('beforeunload', h) }, [dirty])
  const extensions = useMemo(() => [cmTheme, cmHighlight, EditorView.lineWrapping, keymap.of([{ key: 'Mod-s', run: () => true }]), ...(lang ? [lang] : [])], [lang])
  const chars = text?.length ?? 0
  const entries = text ? text.split('\n§\n').filter(c => c.trim()).length : 0
  return (
    <>
      {conflict && (
        <Modal title="File changed on disk" onClose={() => setConflict(null)}>
          <p className="text-sm">{conflict} — most likely the agent wrote to it. Reload to see its version (your edits are lost) or overwrite it.</p>
          <div className="mt-4 flex justify-end gap-2"><Btn kind="ghost" onClick={reload}>Reload</Btn><Btn kind="warn" onClick={() => save(true)}>Overwrite</Btn></div>
        </Modal>)}
      <div className="mb-2 flex min-w-0 items-center gap-2 text-xs">
        <span className="min-w-0 flex-1 truncate font-mono text-[11px]"><span className="text-muted">{profile} / memories / </span><span className="font-medium text-fg">{name}</span></span>
        {text !== null && <span className="hidden font-mono text-[10px] text-muted sm:inline">{entries} § · {chars}{limit ? ` / ${limit}` : ''} chars</span>}
        {limit != null && chars > limit && <Chip>over limit</Chip>}
        {dirty && <Chip>unsaved</Chip>}
        <Btn busy={saving} disabled={!dirty} onClick={() => save()}>Save</Btn>
      </div>
      {f.isLoading || text === null ? <Skeleton rows={6} /> : f.isError ? <Empty title="Could not open" note={(f.error as Error).message} error /> : (
        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-line bg-inset">
          <CodeMirror value={text} onChange={setText} extensions={extensions} height="100%" className="h-full" theme="none" basicSetup={{ foldGutter: false, highlightActiveLine: true, autocompletion: false }} onCreateEditor={v => { view.current = v; setReady(true) }} />
        </div>)}
    </>
  )
}

// ---------------------------------------------------------------- Providers
const STATUS: Record<Provider['status'], { label: string; cls: string }> = {
  ready: { label: 'ready', cls: 'text-working border-working/40' }, needs_config: { label: 'needs config', cls: 'text-needsyou border-needsyou/40' },
  unavailable: { label: 'not installed', cls: 'text-muted border-line' }, missing: { label: 'missing', cls: 'text-error border-error/40' },
}
const visible = (f: Field, values: Record<string, unknown>) => !f.when || Object.entries(f.when).every(([k, v]) => String(values[k] ?? '') === String(v))

function ProvidersTab({ profile }: { profile: string }) {
  const qc = useQueryClient(); const toast = useToast()
  const p = useQuery({ queryKey: ['memory-providers', profile], queryFn: () => get<Providers>(`/api/memory/providers?${q({ profile })}`), staleTime: 60000 })
  const [open, setOpen] = useState<string | null>(null)
  const [activating, setActivating] = useState(false)
  const [restartHint, setRestartHint] = useState(false)
  const refresh = () => qc.invalidateQueries({ queryKey: ['memory-providers', profile] })
  const activate = async (name: string) => {
    setActivating(true)
    try { await post('/api/memory/provider', { profile, name }); toast(name ? `${name} is now the memory provider` : 'Built-in memory files active'); setRestartHint(true); await get(`/api/memory/providers?${q({ profile, fresh: 1 })}`); refresh() }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setActivating(false) }
  }
  if (p.isLoading) return <div className="grid gap-3 lg:grid-cols-2"><Skeleton rows={4} card /><Skeleton rows={4} card /></div>
  if (p.isError) return <GlassCard><Empty title="Could not load providers" note={(p.error as Error).message} error /></GlassCard>
  const active = p.data!.active
  const activeRow = p.data!.providers.find(x => x.name === active)
  return (
    <div className="grid gap-3">
      <GlassCard accent="var(--hq-accent)">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-widest text-muted">Active memory provider · {profile}</p>
            <p className="mt-1 text-sm font-semibold">{active ? (activeRow?.label ?? active) : 'Built-in files (MEMORY.md + USER.md)'}{activeRow && <span className={clsx('ml-2 rounded-full border px-2 py-0.5 font-mono text-[10px]', STATUS[activeRow.status].cls)}>{STATUS[activeRow.status].label}</span>}</p>
            {activeRow?.status === 'missing' && <p className="mt-1 text-xs text-error">config.yaml names a provider that is not installed — Hermes falls back to the built-in files.</p>}
            {restartHint && <p className="mt-1 text-xs text-needsyou">Restart this agent's gateway (Agents page) so running sessions pick the change up.</p>}
          </div>
          {active && <Btn kind="ghost" busy={activating} onClick={() => activate('')}>Use built-in files</Btn>}
        </div>
      </GlassCard>
      {p.data!.providers.map(pr => {
        const st = STATUS[pr.status]; const isOpen = open === pr.name
        return (
          <GlassCard key={pr.name} className={clsx(pr.name === active && 'border-accent/50')}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <button onClick={() => setOpen(isOpen ? null : pr.name)} className="min-w-0 flex-1 text-left" aria-expanded={isOpen}>
                <p className="flex items-center gap-2 text-sm font-semibold"><span>{pr.label || pr.name}</span><span className={clsx('rounded-full border px-2 py-0.5 font-mono text-[10px]', st.cls)}>{st.label}</span>{pr.name === active && <Chip>active</Chip>}</p>
                <p className="mt-1 text-xs text-muted">{pr.description}</p>
              </button>
              <div className="flex gap-2">
                {pr.status === 'ready' && pr.name !== active && <Btn kind="ghost" busy={activating} onClick={() => activate(pr.name)}>Activate</Btn>}
                <Btn kind="ghost" onClick={() => setOpen(isOpen ? null : pr.name)}>{isOpen ? 'Close' : 'Configure'}</Btn>
              </div>
            </div>
            {isOpen && <ProviderForm profile={profile} pr={pr} onDone={refresh} onActivated={() => setRestartHint(true)} />}
          </GlassCard>)
      })}
    </div>
  )
}

function ProviderForm({ profile, pr, onDone, onActivated }: { profile: string; pr: Provider; onDone: () => void; onActivated: () => void }) {
  const toast = useToast()
  const [values, setValues] = useState<Record<string, unknown>>(() => Object.fromEntries(pr.fields.map(f => [f.key, f.kind === 'secret' ? '' : f.value ?? ''])))
  const [show, setShow] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState<'save' | 'activate' | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  useEffect(() => {
    if (!job || job.status !== 'running') return
    const t = setInterval(async () => { const j = await get<Job>(`/api/jobs/${job.id}`); setJob(j); if (j.status !== 'running') { toast(j.status === 'done' ? `${pr.name} installed` : `${pr.name} install failed`, j.status === 'done' ? undefined : 'err'); onDone() } }, 1500)
    return () => clearInterval(t)
  }, [job, pr.name, onDone, toast])
  const submit = async (activate: boolean) => {
    setBusy(activate ? 'activate' : 'save')
    try { await post(`/api/memory/providers/${pr.name}/config`, { profile, values, activate }); toast(activate ? `${pr.name} saved and activated` : 'Saved'); if (activate) onActivated(); onDone() }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(null) }
  }
  const install = async () => { const r = await post<{ job: Job }>(`/api/memory/providers/${pr.name}/setup`, { profile, values }); setJob({ ...r.job, log: '', result: null }) }
  const needsInstall = !pr.setup.dependencies_installed
  return (
    <div className="mt-3 border-t border-line pt-3">
      {needsInstall && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-needsyou/40 bg-needsyou/10 p-2 text-xs">
          <span>Needs <span className="font-mono">{[...pr.setup.pip_dependencies, ...pr.setup.external_dependencies.map(d => d.name)].join(', ')}</span> installed in Hermes' environment.</span>
          <Btn kind="ghost" busy={job?.status === 'running'} onClick={install}>Install</Btn>
        </div>)}
      {job && <pre className="mb-3 max-h-40 overflow-auto rounded-lg border border-line bg-inset p-2 font-mono text-[11px] text-muted">{job.status === 'running' ? 'Installing…\n' : ''}{job.result?.results?.map(r => `${r.status}  ${r.name ?? ''}\n${r.output ?? ''}`).join('\n') || job.log || ''}</pre>}
      {pr.fields.length === 0 && <p className="text-xs text-muted">This provider has no settings.</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        {pr.fields.filter(f => visible(f, values)).map(f => (
          <label key={f.key} className="block text-xs">
            <span className="mb-1 flex items-center gap-1.5 text-muted">{f.label}{f.required && <Chip>required</Chip>}{f.kind === 'secret' && f.is_set && <Chip tone="muted">set</Chip>}{f.url && <a href={f.url} target="_blank" rel="noreferrer" className="text-accent-2 hover:underline">help ↗</a>}</span>
            {f.kind === 'boolean' ? <input type="checkbox" checked={Boolean(values[f.key])} onChange={e => setValues(v => ({ ...v, [f.key]: e.target.checked }))} />
              : f.kind === 'select' ? <select value={String(values[f.key] ?? '')} onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))} className="hq-select w-full appearance-none rounded-lg border border-line bg-inset py-1.5 pl-2 pr-8 text-sm outline-none focus:border-accent">{f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
              : f.kind === 'secret' ? <span className="flex gap-1"><input type={show[f.key] ? 'text' : 'password'} value={String(values[f.key] ?? '')} onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))} placeholder={f.is_set ? 'leave blank to keep the stored value' : f.placeholder} autoComplete="off" className="w-full rounded-lg border border-line bg-inset px-3 py-1.5 text-sm outline-none focus:border-accent" /><button type="button" onClick={() => setShow(s => ({ ...s, [f.key]: !s[f.key] }))} className="rounded-lg border border-line px-2 font-mono text-[10px] text-muted">{show[f.key] ? 'hide' : 'show'}</button></span>
              : <input type={f.kind === 'text' ? 'text' : 'number'} min={f.minimum ?? undefined} max={f.maximum ?? undefined} step={f.step ?? undefined} value={String(values[f.key] ?? '')} onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))} placeholder={f.placeholder} className="w-full rounded-lg border border-line bg-inset px-3 py-1.5 text-sm outline-none focus:border-accent" />}
            {f.description && <span className="mt-1 block text-[11px] text-muted">{f.description}</span>}
          </label>))}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Btn kind="ghost" busy={busy === 'save'} onClick={() => submit(false)}>Save</Btn>
        <Btn busy={busy === 'activate'} disabled={needsInstall} title={needsInstall ? 'Install first' : undefined} onClick={() => submit(true)}>Save &amp; activate</Btn>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- Graph
const PALETTE = ['var(--hq-accent)', 'var(--hq-working)', 'var(--hq-needsyou)', 'var(--hq-queued)', 'var(--hq-done)', 'var(--hq-accent-2)', 'var(--hq-error)', 'var(--hq-backlog)']
function layout(g: Graph, w: number, h: number) {
  // Small deterministic force layout: clusters seeded on a ring, springs on edges, repulsion between all nodes.
  const cats = Array.from(new Set(g.nodes.map(n => n.category)))
  const pos = new Map<string, { x: number; y: number; vx: number; vy: number }>()
  let seed = 7; const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
  g.nodes.forEach(n => { const ci = cats.indexOf(n.category); const a = (ci / Math.max(1, cats.length)) * Math.PI * 2; const r = Math.min(w, h) * 0.3; pos.set(n.id, { x: w / 2 + Math.cos(a) * r + (rnd() - 0.5) * 80, y: h / 2 + Math.sin(a) * r + (rnd() - 0.5) * 80, vx: 0, vy: 0 }) })
  const ids = g.nodes.map(n => n.id)
  const N = Math.max(1, ids.length)
  const cell = Math.sqrt((w * h) / N)                 // typical spacing on this canvas
  const rep = cell * cell * 0.9, rest = cell * 0.8
  const pad = w < 640 ? 70 : 28                       // room for labels on narrow canvases
  for (let it = 0; it < 300; it++) {
    const k = Math.max(0.15, 1 - it / 300)
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
      const a = pos.get(ids[i])!, b = pos.get(ids[j])!; let dx = a.x - b.x, dy = a.y - b.y; const d2 = Math.max(100, dx * dx + dy * dy); const f = rep / d2
      const d = Math.sqrt(d2); dx = dx / d * f; dy = dy / d * f; a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy
    }
    g.edges.forEach(e => { const a = pos.get(e.source), b = pos.get(e.target); if (!a || !b) return; const dx = b.x - a.x, dy = b.y - a.y; const d = Math.sqrt(dx * dx + dy * dy) || 1; const f = (d - rest) * 0.05; a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f })
    pos.forEach(p => { p.vx += (w / 2 - p.x) * 0.02; p.vy += (h / 2 - p.y) * 0.02; p.x = Math.max(pad, Math.min(w - pad, p.x + p.vx * k * 0.5)); p.y = Math.max(pad, Math.min(h - pad, p.y + p.vy * k * 0.5)); p.vx *= 0.5; p.vy *= 0.5 })
  }
  return { pos, cats }
}

function GraphTab({ profile, setParams }: { profile: string; setParams: (p: Record<string, string>) => void }) {
  const g = useQuery({ queryKey: ['memory-graph', profile], queryFn: () => get<Graph>(`/api/memory/graph?${q({ profile })}`), staleTime: 30000 })
  const [sel, setSel] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 800, h: 520 })
  useEffect(() => { const el = box.current; if (!el) return; const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientWidth < 640 ? Math.round(el.clientWidth * 1.3) : Math.max(360, Math.min(640, el.clientWidth * 0.62)) })); ro.observe(el); return () => ro.disconnect() }, [])
  const lay = useMemo(() => g.data ? layout(g.data, size.w, size.h) : null, [g.data, size])
  const node = useQuery({ queryKey: ['memory-node', profile, sel], queryFn: () => get<{ kind: string; label: string; content: string }>(`/api/memory/graph/node?${q({ profile, id: sel! })}`), enabled: !!sel })
  const selNode = g.data?.nodes.find(n => n.id === sel)
  const neighbours = useMemo(() => new Set(g.data?.edges.filter(e => e.source === sel || e.target === sel).flatMap(e => [e.source, e.target]) ?? []), [g.data, sel])
  if (g.isLoading) return <GlassCard><Skeleton rows={8} /></GlassCard>
  if (g.isError) return <GlassCard><Empty title="Could not build the graph" note={(g.error as Error).message} error /></GlassCard>
  const d = g.data!; const s = d.stats as Record<string, number>
  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[1fr_20rem]">
      <div className="min-w-0">
        <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[['Nodes', d.nodes.length], ['Edges', d.edges.length], ['Edges / node', s.edges_per_node], ['Isolated', `${s.isolated_pct ?? 0}%`], ['Learned skills', s.learned_skills], ['Agent-created', s.agent_created], ['Used', s.used], ['Memory cards', s.memory_nodes]].map(([l, v]) => (
            <GlassCard key={String(l)} className="py-2"><p className="font-mono text-[10px] uppercase tracking-widest text-muted">{l}</p><p className="font-mono text-lg font-semibold">{v ?? '—'}</p></GlassCard>))}
        </div>
        <GlassCard className="!p-2">
          <div ref={box} className="min-w-0">
            {d.nodes.length === 0 ? <Empty title="Nothing learned yet" note="Agent-created or used skills and memory entries appear here." /> : lay && (
              <svg width={size.w} height={size.h} role="img" aria-label="Learning graph" className="block max-w-full">
                {d.edges.map((e, i) => { const a = lay.pos.get(e.source), b = lay.pos.get(e.target); if (!a || !b) return null; const hi = sel && (e.source === sel || e.target === sel); return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={hi ? 'var(--hq-accent-2)' : 'var(--hq-border)'} strokeWidth={hi ? 1.6 : 1} /> })}
                {d.nodes.map(n => { const p = lay.pos.get(n.id)!; const r = n.kind === 'memory' ? 6 : 7 + Math.min(10, Math.sqrt(n.useCount)); const c = PALETTE[lay.cats.indexOf(n.category) % PALETTE.length]; const dim = sel && sel !== n.id && !neighbours.has(n.id)
                  return <g key={n.id} onClick={() => setSel(n.id)} className="cursor-pointer" opacity={dim ? 0.3 : 1}>
                    {n.kind === 'memory' ? <rect x={p.x - r} y={p.y - r} width={r * 2} height={r * 2} rx={2} fill={c} stroke={sel === n.id ? 'var(--hq-text)' : 'none'} strokeWidth={1.5} /> : <circle cx={p.x} cy={p.y} r={r} fill={c} stroke={sel === n.id ? 'var(--hq-text)' : 'none'} strokeWidth={1.5} />}
                    <text x={p.x} y={p.y + r + 11} textAnchor="middle" fontSize={10} fill="var(--hq-muted)" fontFamily="var(--hq-font-mono)">{n.label.length > (size.w < 640 ? 16 : 22) ? n.label.slice(0, size.w < 640 ? 15 : 21) + '…' : n.label}</text>
                    <title>{n.label} · {n.category} · used {n.useCount}×</title></g> })}
              </svg>)}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 px-1 font-mono text-[10px] text-muted">
            {lay?.cats.map((c, i) => <span key={c} className="flex items-center gap-1"><span className="inline-block size-2 rounded-full" style={{ background: PALETTE[i % PALETTE.length] }} />{c} ({d.clusters.find(x => x.category === c)?.count ?? ''})</span>)}
            <span>● skill (size = uses) · ■ memory entry</span>
          </div>
        </GlassCard>
      </div>
      <GlassCard className="min-w-0 self-start">
        {!sel ? <Empty title="Pick a node" note="Click a skill or memory entry to read it." /> : (
          <>
            <p className="font-mono text-[10px] uppercase tracking-widest text-muted">{selNode?.kind} · {selNode?.category}</p>
            <p className="mt-1 break-words text-sm font-semibold">{selNode?.label}</p>
            <p className="mt-1 font-mono text-[10px] text-muted">{selNode?.kind === 'skill' ? `used ${selNode.useCount}× · ${selNode.createdBy} · ${selNode.state}` : `from ${selNode?.memorySource === 'profile' ? 'USER.md' : 'MEMORY.md'}`}{selNode?.timestamp ? ` · ${ago(selNode.timestamp)}` : ''}</p>
            {node.isLoading ? <Skeleton rows={4} /> : node.isError ? <p className="mt-2 text-xs text-error">{(node.error as Error).message}</p> : <pre className="mt-2 max-h-[50dvh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-inset p-2 font-mono text-[11px]">{node.data?.content}</pre>}
            {selNode?.kind === 'memory' && <Btn kind="ghost" className="mt-2" onClick={() => setParams({ tab: 'files', file: selNode.memorySource === 'profile' ? 'USER.md' : 'MEMORY.md' })}>Open in editor</Btn>}
          </>)}
      </GlassCard>
    </div>
  )
}
