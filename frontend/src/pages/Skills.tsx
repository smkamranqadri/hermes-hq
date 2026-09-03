import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import CodeMirror from '@uiw/react-codemirror'
import { EditorView } from '@codemirror/view'
import { cmTheme, cmHighlight, useLanguage } from '../components/editor'
import { get, post } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Skeleton, Chip, Spinner } from '../components/ui'
import { Modal, ConfirmModal, Field, TextInput, TextArea, Btn } from '../components/Modal'
import { Menu, MenuItem } from '../components/Menu'
import { Markdown } from '../components/chat/Markdown'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'
import { AgentSwitcher } from '../components/AgentSwitcher'
import { saveDraft } from '../components/chat/composer'

// Group 6-3 — Skills browser: installed skills per agent (Hermes' own scan + rules) and the skills hub.
type Skill = { name: string; description: string; category: string; enabled: boolean; usage: number; provenance: 'bundled' | 'hub' | 'agent'; path: string; tags: string[]; version: string; author: string; homepage: string; mtime: number | null }
type HubResult = { name: string; identifier: string; source: string; trust_level: 'builtin' | 'trusted' | 'community' | string; description: string; repo?: string | null; tags?: string[] }
type Sources = { sources: { id: string; label: string; available?: boolean; rate_limited?: boolean; searchable: boolean }[]; index_available: boolean; featured: HubResult[]; installed: Record<string, unknown> }
type Search = { results: HubResult[]; source_counts: Record<string, number>; timed_out: string[]; installed: Record<string, unknown> }
type Preview = { name: string; description: string; source: string; identifier: string; trust_level: string; repo?: string | null; tags: string[]; skill_md: string; files: string[] }
type Scan = { verdict: string; summary: string; policy: 'allow' | 'ask' | 'block'; policy_reason: string; findings: { severity: string; category: string; file: string; line?: number | null; description: string }[]; severity_counts: Record<string, number>; trust_level: string }
type Job = { id: string; kind: string; label: string; status: 'running' | 'done' | 'failed'; log: string; result: unknown }

const q = (o: Record<string, string | number | undefined>) => Object.entries(o).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
const ORIGIN: Record<Skill['provenance'], { label: string; cls: string }> = { bundled: { label: 'built-in', cls: 'border-line text-muted' }, agent: { label: 'agent / local', cls: 'border-needsyou/50 text-needsyou' }, hub: { label: 'hub', cls: 'border-working/50 text-working' } }
const TRUST: Record<string, string> = { builtin: 'border-working/50 text-working', trusted: 'border-accent/50 text-accent-2', community: 'border-needsyou/50 text-needsyou' }
const VERDICT: Record<string, { label: string; cls: string }> = { safe: { label: 'Benign', cls: 'border-working/50 text-working' }, low: { label: 'Benign', cls: 'border-working/50 text-working' }, medium: { label: 'Caution', cls: 'border-needsyou/50 text-needsyou' }, high: { label: 'Warning', cls: 'border-error/50 text-error' }, dangerous: { label: 'Warning', cls: 'border-error/50 text-error' } }
const TEMPLATE = (name: string) => `---\nname: ${name}\ndescription: "One line: when should an agent reach for this skill?"\nversion: 1.0.0\nauthor: ${'Kamran'}\nmetadata:\n  hermes:\n    tags: []\n---\n\n# ${name}\n\n## When to use\n\n## Steps\n\n1. \n`
const icon = (s: { category?: string }) => ({ 'autonomous-ai-agents': '🤖', 'software-development': '💻', devops: '⚙️', research: '🔎', productivity: '⚡', creative: '🎨', media: '🎬', email: '✉️', github: '🐙', 'note-taking': '📝', 'social-media': '📣', apple: '', mlops: '🧪', 'smart-home': '🏠', uiux: '🎛️' } as Record<string, string>)[s.category ?? ''] || '🧩'

export function Skills() {
  usePageTitle('Skills')
  const [params, setParamsRaw] = useSearchParams()
  const profile = params.get('profile') || 'orchestrator'
  const tab = params.get('tab') === 'hub' ? 'hub' : 'installed'
  const setParams = (patch: Record<string, string>) => setParamsRaw(p => { const n = new URLSearchParams(p); for (const [k, v] of Object.entries(patch)) { if (v) n.set(k, v); else n.delete(k) } return n }, { replace: true })
  const [sheet, setSheet] = useState(false)
  return (
    <section className="mx-auto max-w-7xl p-4 sm:p-6">
      <PageHeader crumb="skills" title="Skills" right={
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <AgentSwitcher value={profile} onChange={p => setParams({ profile: p })} className="w-full sm:w-40" />
          <div role="tablist" className="flex rounded-full border border-line bg-glass p-0.5 font-mono text-[10px] uppercase tracking-wider">
            {(['installed', 'hub'] as const).map(t => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setParams({ tab: t })} className={clsx('rounded-full px-2.5 py-1', tab === t ? 'bg-accent/20 text-fg' : 'text-muted hover:text-fg')}>{t}</button>)}
          </div>
          {tab === 'installed' && <Btn kind="ghost" className="lg:hidden" onClick={() => setSheet(true)} aria-label="Filters">Filters</Btn>}
        </div>} />
      {tab === 'installed' ? <Installed profile={profile} sheet={sheet} setSheet={setSheet} /> : <Hub profile={profile} />}
    </section>
  )
}

// ---------------------------------------------------------------- Installed
function Installed({ profile, sheet, setSheet }: { profile: string; sheet: boolean; setSheet: (b: boolean) => void }) {
  const qc = useQueryClient(); const toast = useToast(); const nav = useNavigate()
  const list = useQuery({ queryKey: ['skills', profile], queryFn: () => get<{ skills: Skill[] }>(`/api/skills?${q({ profile })}`), staleTime: 30000 })
  const [search, setSearch] = useState(''); const [cat, setCat] = useState(''); const [origin, setOrigin] = useState(''); const [sort, setSort] = useState<'name' | 'category' | 'usage'>('name')
  const [open, setOpen] = useState<string | null>(null); const [creating, setCreating] = useState(false); const [learn, setLearn] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  useJob(job, setJob, () => { qc.invalidateQueries({ queryKey: ['skills', profile] }); void get(`/api/skills?${q({ profile, fresh: 1 })}`).then(() => qc.invalidateQueries({ queryKey: ['skills', profile] })) })
  const skills = list.data?.skills ?? []
  const cats = useMemo(() => Array.from(new Set(skills.map(s => s.category))).sort(), [skills])
  const shown = useMemo(() => {
    const t = search.trim().toLowerCase()
    const tier = (s: Skill) => !t ? 0 : s.name.toLowerCase().includes(t) ? 0 : s.tags.some(x => x.toLowerCase().includes(t)) ? 1 : s.description.toLowerCase().includes(t) ? 2 : 3
    return skills.filter(s => (!cat || s.category === cat) && (!origin || s.provenance === origin) && tier(s) < 3)
      .sort((a, b) => tier(a) - tier(b) || (sort === 'usage' ? b.usage - a.usage : sort === 'category' ? a.category.localeCompare(b.category) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name)))
  }, [skills, search, cat, origin, sort])
  const toggle = async (s: Skill) => {
    setBusy(s.name)
    try { await post('/api/skills/toggle', { profile, name: s.name, enabled: !s.enabled }); qc.setQueryData(['skills', profile], (o: { skills: Skill[] } | undefined) => o ? { skills: o.skills.map(x => x.name === s.name ? { ...x, enabled: !s.enabled } : x) } : o); toast(`${s.name} ${s.enabled ? 'disabled' : 'enabled'} — live within ~30 s`) }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(null) }
  }
  const run = async (url: string, body: object, label: string) => { try { const r = await post<{ job: Job }>(url, body); setJob({ ...r.job, log: '', result: null }); toast(`${label} started`) } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } }
  const filters = (
    <div className="flex min-h-0 flex-col gap-2">
      <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search name, tag, description" aria-label="Search skills" className="w-full rounded-lg border border-line bg-inset px-3 py-1.5 text-sm outline-none placeholder:text-muted focus:border-accent" />
      <select value={origin} onChange={e => setOrigin(e.target.value)} aria-label="Origin" className="hq-select w-full appearance-none rounded-lg border border-line bg-inset py-1.5 pl-2 pr-8 text-sm outline-none focus:border-accent"><option value="">All origins</option><option value="bundled">Built-in</option><option value="agent">Agent / local</option><option value="hub">Hub</option></select>
      <select value={sort} onChange={e => setSort(e.target.value as typeof sort)} aria-label="Sort" className="hq-select w-full appearance-none rounded-lg border border-line bg-inset py-1.5 pl-2 pr-8 text-sm outline-none focus:border-accent"><option value="name">Name A–Z</option><option value="category">Category</option><option value="usage">Most used</option></select>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <button onClick={() => setCat('')} className={clsx('block w-full rounded-lg px-2 py-1 text-left text-xs', !cat ? 'bg-accent/15 text-fg' : 'text-muted hover:text-fg')}>All categories <span className="font-mono text-[10px]">{skills.length}</span></button>
        {cats.map(c => <button key={c} onClick={() => setCat(c === cat ? '' : c)} className={clsx('block w-full rounded-lg px-2 py-1 text-left text-xs', cat === c ? 'bg-accent/15 text-fg' : 'text-muted hover:text-fg')}>{icon({ category: c })} {c || '(no category)'} <span className="font-mono text-[10px]">{skills.filter(s => s.category === c).length}</span></button>)}
      </div>
      <div className="flex flex-wrap gap-2 border-t border-line pt-2">
        <Btn onClick={() => { setCreating(true); setSheet(false) }}>New skill</Btn>
        <Btn kind="ghost" onClick={() => { setLearn(true); setSheet(false) }}>Learn a skill</Btn>
        <Menu button={<span>⋯</span>}><MenuItem onClick={() => run('/api/skills/hub/check', { profile }, 'Update check')}>Check for updates</MenuItem><MenuItem onClick={() => run('/api/skills/hub/update', { profile }, 'Update all')}>Update all hub skills</MenuItem><MenuItem onClick={() => run('/api/skills/audit', { profile }, 'Audit')}>Audit hub skills</MenuItem></Menu>
      </div>
    </div>
  )
  const openSkill = open ? skills.find(s => s.name === open) : null
  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[16rem_1fr]">
      <GlassCard className="hidden min-w-0 lg:flex lg:h-[calc(100dvh-12.5rem)] lg:flex-col">{filters}</GlassCard>
      {sheet && <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-label="Filters" data-sheet><div className="absolute inset-0 bg-bg/60" onClick={() => setSheet(false)} /><div className="absolute inset-x-0 bottom-0 flex h-[80dvh] flex-col rounded-t-2xl border border-line hq-menu p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-2xl"><div className="mx-auto mb-2 h-1 w-10 shrink-0 rounded-full bg-line" />{filters}</div></div>}
      <div className="min-w-0">
        {job && <JobCard job={job} onClose={() => setJob(null)} />}
        {list.isLoading ? <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} rows={3} card />)}</div>
          : list.isError ? <GlassCard><Empty title="Could not list skills" note={(list.error as Error).message} error /></GlassCard>
          : shown.length === 0 ? <GlassCard><Empty title="No skills match" /></GlassCard> : (
            <>
              <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-muted">{shown.length} of {skills.length} · {skills.filter(s => s.enabled).length} enabled</p>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {shown.map(s => (
                  <GlassCard key={s.name} className={clsx('flex min-w-0 flex-col gap-2 py-3', !s.enabled && 'opacity-60')}>
                    <div className="flex min-w-0 items-start gap-2">
                      <span className="text-lg leading-none">{icon(s)}</span>
                      <button onClick={() => setOpen(s.name)} className="min-w-0 flex-1 text-left"><p className="truncate text-sm font-semibold">{s.name}</p><p className="truncate font-mono text-[10px] text-muted">{s.author ? `by ${s.author}` : s.category}{s.version ? ` · v${s.version}` : ''}</p></button>
                      <button role="switch" aria-checked={s.enabled} aria-label={`${s.enabled ? 'Disable' : 'Enable'} ${s.name}`} disabled={busy === s.name} onClick={() => toggle(s)} className={clsx('relative h-5 w-9 shrink-0 rounded-full border transition', s.enabled ? 'border-accent bg-accent/60' : 'border-line bg-inset')}><span className={clsx('absolute top-0.5 size-3.5 rounded-full bg-fg transition', s.enabled ? 'left-4' : 'left-0.5')} /></button>
                    </div>
                    <p className="line-clamp-2 text-xs text-muted">{s.description}</p>
                    <div className="mt-auto flex flex-wrap items-center gap-1 font-mono text-[10px]">
                      <span className={clsx('rounded-full border px-1.5 py-0.5', ORIGIN[s.provenance].cls)}>{ORIGIN[s.provenance].label}</span>
                      <span className="rounded-full border border-line px-1.5 py-0.5 text-muted">{s.category || 'no category'}</span>
                      {s.tags.slice(0, 2).map(t => <span key={t} className="rounded-full border border-line px-1.5 py-0.5 text-muted">{t}</span>)}
                      <span className="ml-auto text-muted" title="times used">{s.usage}× used</span>
                    </div>
                  </GlassCard>))}
              </div>
            </>)}
      </div>
      {openSkill && <SkillDetail profile={profile} skill={openSkill} onClose={() => setOpen(null)} onJob={j => setJob(j)} />}
      {creating && <CreateModal profile={profile} onClose={() => setCreating(false)} onCreated={() => { setCreating(false); qc.invalidateQueries({ queryKey: ['skills', profile] }) }} />}
      {learn && <LearnModal onClose={() => setLearn(false)} onGo={text => { saveDraft(profile, undefined, text); nav(`/chat/${profile}`) }} />}
    </div>
  )
}

function useJob(job: Job | null, setJob: (j: Job) => void, onFinish: () => void) {
  const toast = useToast()
  useEffect(() => {
    if (!job || job.status !== 'running') return
    const t = setInterval(async () => { const j = await get<Job>(`/api/jobs/${job.id}`); setJob(j); if (j.status !== 'running') { toast(`${j.label}: ${j.status}`, j.status === 'done' ? undefined : 'err'); onFinish() } }, 1500)
    return () => clearInterval(t)
  }, [job?.id, job?.status]) // eslint-disable-line react-hooks/exhaustive-deps
}

function JobCard({ job, onClose }: { job: Job; onClose: () => void }) {
  return (
    <GlassCard className="mb-3 py-3">
      <div className="flex items-center gap-2 text-xs"><span className={clsx('rounded-full border px-2 py-0.5 font-mono text-[10px]', job.status === 'running' ? 'border-accent/50 text-accent-2' : job.status === 'done' ? 'border-working/50 text-working' : 'border-error/50 text-error')}>{job.status}</span><span className="font-medium">{job.label}</span>{job.status === 'running' && <Spinner className="size-3" />}<button onClick={onClose} className="ml-auto font-mono text-[10px] text-muted hover:text-fg">dismiss</button></div>
      {job.log && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-inset p-2 font-mono text-[11px] text-muted">{job.log.replace(/\x1b\[[0-9;]*m/g, '')}</pre>}
    </GlassCard>
  )
}

function SkillDetail({ profile, skill, onClose, onJob }: { profile: string; skill: Skill; onClose: () => void; onJob: (j: Job) => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const f = useQuery({ queryKey: ['skill-read', profile, skill.name], queryFn: () => get<{ content: string; path: string }>(`/api/skills/read?${q({ profile, name: skill.name })}`) })
  const [edit, setEdit] = useState(false); const [text, setText] = useState<string | null>(null); const [saving, setSaving] = useState(false); const [uninstallOpen, setUninstallOpen] = useState(false); const [actionBusy, setActionBusy] = useState(false)
  const lang = useLanguage('SKILL.md')
  const ext = useMemo(() => [cmTheme, cmHighlight, EditorView.lineWrapping, ...(lang ? [lang] : [])], [lang])
  const save = async () => { if (text === null) return; setSaving(true); try { await post('/api/skills/write', { profile, name: skill.name, content: text }); qc.setQueryData(['skill-read', profile, skill.name], (o: { content: string; path: string } | undefined) => o ? { ...o, content: text } : o); qc.invalidateQueries({ queryKey: ['skills', profile] }); setEdit(false); toast('SKILL.md saved') } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setSaving(false) } }
  const act = async (url: string, body: object) => { setActionBusy(true); try { const r = await post<{ job: Job }>(url, body); onJob({ ...r.job, log: '', result: null }); onClose() } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setActionBusy(false) } }
  return (
    <>
      <Modal title={`${icon(skill)} ${skill.name}`} onClose={onClose}>
      <div className="flex flex-wrap items-center gap-1 font-mono text-[10px]">
        <span className={clsx('rounded-full border px-1.5 py-0.5', ORIGIN[skill.provenance].cls)}>{ORIGIN[skill.provenance].label}</span><span className="rounded-full border border-line px-1.5 py-0.5 text-muted">{skill.category}</span>{skill.version && <span className="text-muted">v{skill.version}</span>}<span className="text-muted">· {skill.usage}× used</span>{skill.homepage && <a href={skill.homepage} target="_blank" rel="noreferrer" className="text-accent-2 hover:underline">homepage ↗</a>}
      </div>
      {skill.tags.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{skill.tags.map(t => <Chip key={t} tone="muted">{t}</Chip>)}</div>}
      <p className="mt-2 truncate font-mono text-[10px] text-muted" title={f.data?.path ?? skill.path}>Source: {f.data?.path ?? skill.path}</p>
      <div className="mt-3 max-h-[55dvh] min-h-[12rem] overflow-auto rounded-lg border border-line bg-inset p-3">
        {f.isLoading ? <Skeleton rows={6} /> : f.isError ? <Empty title="No SKILL.md" note={(f.error as Error).message} error />
          : edit ? <CodeMirror value={text ?? f.data!.content} onChange={setText} extensions={ext} theme="none" basicSetup={{ foldGutter: false, autocompletion: false }} />
          : <div className="text-sm"><Markdown text={f.data!.content} /></div>}
      </div>
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        {edit ? <><Btn kind="ghost" onClick={() => { setEdit(false); setText(null) }}>Cancel</Btn><Btn busy={saving} disabled={text === null || text === f.data?.content} onClick={save}>Save</Btn></>
          : <>
            {skill.provenance === 'hub' && <><Btn kind="ghost" onClick={() => void act('/api/skills/hub/update', { profile, name: skill.name })}>Update</Btn><Btn kind="warn" onClick={() => setUninstallOpen(true)} disabled={actionBusy}>Uninstall</Btn></>}
            {f.data && <Btn kind="ghost" onClick={() => { setText(f.data!.content); setEdit(true) }}>Edit SKILL.md</Btn>}
            <Btn onClick={onClose}>Close</Btn>
          </>}
      </div>
      </Modal>
    {uninstallOpen && <ConfirmModal title="Uninstall skill" message={`Uninstall ${skill.name} from ${profile}?`} confirmLabel="Uninstall" busy={actionBusy} onClose={() => setUninstallOpen(false)} onConfirm={() => act('/api/skills/hub/uninstall', { profile, name: skill.name })} />}
    </>
  )
}

function CreateModal({ profile, onClose, onCreated }: { profile: string; onClose: () => void; onCreated: () => void }) {
  const toast = useToast()
  const [name, setName] = useState(''); const [category, setCategory] = useState(''); const [content, setContent] = useState(''); const [busy, setBusy] = useState(false)
  const lang = useLanguage('SKILL.md'); const ext = useMemo(() => [cmTheme, cmHighlight, EditorView.lineWrapping, ...(lang ? [lang] : [])], [lang])
  const body = content || TEMPLATE(name || 'my-skill')
  const submit = async () => { setBusy(true); try { await post('/api/skills/create', { profile, name: name.trim(), category: category.trim() || undefined, content: body }); toast(`${name} created`); onCreated() } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) } }
  return (
    <Modal title="New skill" onClose={onClose}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" hint="lowercase, dashes"><TextInput value={name} onChange={e => setName(e.target.value)} placeholder="my-skill" /></Field>
        <Field label="Category" hint="folder under skills/ (optional)"><TextInput value={category} onChange={e => setCategory(e.target.value)} placeholder="productivity" /></Field>
      </div>
      <div className="mt-3 max-h-[45dvh] overflow-auto rounded-lg border border-line bg-inset"><CodeMirror value={body} onChange={setContent} extensions={ext} theme="none" basicSetup={{ foldGutter: false, autocompletion: false }} /></div>
      <p className="mt-2 text-[11px] text-muted">Saved through Hermes' own skill writer: frontmatter is validated and the security scan runs before the file lands in <span className="font-mono">{profile}</span>'s skills folder.</p>
      <div className="mt-3 flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn busy={busy} disabled={!name.trim()} onClick={submit}>Create</Btn></div>
    </Modal>
  )
}

function LearnModal({ onClose, onGo }: { onClose: () => void; onGo: (text: string) => void }) {
  const [path, setPath] = useState(''); const [url, setUrl] = useState(''); const [text, setText] = useState('')
  const prompt = ['/learn', path.trim(), url.trim(), text.trim()].filter(Boolean).join(' ')
  return (
    <Modal title="Learn a skill" onClose={onClose}>
      <p className="text-xs text-muted">The agent reads what you point it at and writes a SKILL.md with its <span className="font-mono">skill_manage</span> tool. This opens a chat with the prompt ready to send.</p>
      <div className="mt-3 grid gap-3">
        <Field label="Local file or folder"><TextInput value={path} onChange={e => setPath(e.target.value)} placeholder="/opt/data/projects/…/docs" /></Field>
        <Field label="URL"><TextInput value={url} onChange={e => setUrl(e.target.value)} placeholder="https://…" /></Field>
        <Field label="Or describe it"><TextArea value={text} onChange={e => setText(e.target.value)} placeholder="How we release: run the checklist, tag, …" /></Field>
      </div>
      <p className="mt-2 truncate font-mono text-[11px] text-muted" title={prompt}>{prompt}</p>
      <div className="mt-3 flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn disabled={prompt === '/learn'} onClick={() => onGo(prompt)}>Open in chat</Btn></div>
    </Modal>
  )
}

// ---------------------------------------------------------------- Hub
function Hub({ profile }: { profile: string }) {
  const qc = useQueryClient(); const toast = useToast()
  const src = useQuery({ queryKey: ['hub-sources', profile], queryFn: () => get<Sources>(`/api/skills/hub/sources?${q({ profile })}`), staleTime: 300000 })
  const [input, setInput] = useState(''); const [query, setQuery] = useState(''); const [source, setSource] = useState('all')
  const res = useQuery({ queryKey: ['hub-search', profile, query, source], queryFn: () => get<Search>(`/api/skills/hub/search?${q({ profile, q: query, source })}`), enabled: query.length > 0, staleTime: 300000 })
  const [open, setOpen] = useState<HubResult | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  useJob(job, setJob, () => { qc.invalidateQueries({ queryKey: ['hub-sources', profile] }); qc.invalidateQueries({ queryKey: ['skills', profile] }); void get(`/api/skills?${q({ profile, fresh: 1 })}`) })
  const installed = { ...(src.data?.installed ?? {}), ...(res.data?.installed ?? {}) }
  const results = query ? res.data?.results ?? [] : src.data?.featured ?? []
  const install = async (r: HubResult) => { try { const j = await post<{ job: Job }>('/api/skills/hub/install', { profile, identifier: r.identifier }); setJob({ ...j.job, log: '', result: null }); setOpen(null); toast(`Installing ${r.name}…`) } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } }
  const updateAll = async () => { const j = await post<{ job: Job }>('/api/skills/hub/update', { profile }); setJob({ ...j.job, log: '', result: null }) }
  return (
    <div className="min-w-0">
      <GlassCard className="mb-3 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') setQuery(input.trim()) }} placeholder="Search the skills hub (Enter)" aria-label="Search hub" className="min-w-0 flex-1 rounded-lg border border-line bg-inset px-3 py-1.5 text-sm outline-none placeholder:text-muted focus:border-accent" />
          <select value={source} onChange={e => setSource(e.target.value)} aria-label="Source" className="hq-select appearance-none rounded-lg border border-line bg-inset py-1.5 pl-2 pr-8 text-sm outline-none focus:border-accent"><option value="all">All sources</option>{src.data?.sources.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select>
          <Btn onClick={() => setQuery(input.trim())} busy={res.isFetching}>Search</Btn>
          <Btn kind="ghost" onClick={updateAll}>Update all</Btn>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-muted">
          {src.isLoading ? <span>connecting to hubs…</span> : src.data?.sources.map(s => <span key={s.id} className="flex items-center gap-1"><span className={clsx('inline-block size-1.5 rounded-full', s.available === false || s.rate_limited ? 'bg-needsyou' : 'bg-working')} />{s.label}{s.rate_limited ? ' (rate-limited)' : ''}{res.data?.source_counts[s.id] != null ? ` ${res.data.source_counts[s.id]}` : ''}</span>)}
          {res.data?.timed_out.length ? <span className="text-needsyou">timed out: {res.data.timed_out.join(', ')}</span> : null}
        </div>
      </GlassCard>
      {job && <JobCard job={job} onClose={() => setJob(null)} />}
      {res.isError && <GlassCard className="mb-3"><Empty title="Search failed" note={(res.error as Error).message} error /></GlassCard>}
      <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-muted">{query ? `${results.length} results for “${query}”` : 'Featured'}</p>
      {(res.isFetching && query) || src.isLoading ? <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} rows={3} card />)}</div>
        : results.length === 0 ? <GlassCard><Empty title={query ? 'No results' : 'Type to search the hub'} /></GlassCard> : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {results.map(r => {
              const isIn = r.identifier in installed || r.name in installed
              return (
                <GlassCard key={r.identifier} className="flex min-w-0 flex-col gap-2 py-3">
                  <button onClick={() => setOpen(r)} className="min-w-0 text-left"><p className="truncate text-sm font-semibold">{r.name}</p><p className="truncate font-mono text-[10px] text-muted">{r.repo || r.identifier}</p></button>
                  <p className="line-clamp-2 text-xs text-muted">{r.description}</p>
                  <div className="mt-auto flex flex-wrap items-center gap-1 font-mono text-[10px]">
                    <span className={clsx('rounded-full border px-1.5 py-0.5', TRUST[r.trust_level] ?? 'border-line text-muted')}>{r.trust_level}</span><span className="rounded-full border border-line px-1.5 py-0.5 text-muted">{r.source}</span>{isIn && <Chip>installed</Chip>}
                    <Btn kind="ghost" className="ml-auto" onClick={() => setOpen(r)}>Open</Btn>
                  </div>
                </GlassCard>)
            })}
          </div>)}
      {open && <HubDetail profile={profile} r={open} installed={open.identifier in installed || open.name in installed} onClose={() => setOpen(null)} onInstall={() => install(open)} />}
    </div>
  )
}

function HubDetail({ profile, r, installed, onClose, onInstall }: { profile: string; r: HubResult; installed: boolean; onClose: () => void; onInstall: () => void }) {
  const [tab, setTab] = useState<'readme' | 'scan'>('readme')
  const pv = useQuery({ queryKey: ['hub-preview', profile, r.identifier], queryFn: () => get<Preview>(`/api/skills/hub/preview?${q({ profile, identifier: r.identifier })}`), staleTime: 300000 })
  const sc = useQuery({ queryKey: ['hub-scan', profile, r.identifier], queryFn: () => get<Scan>(`/api/skills/hub/scan?${q({ profile, identifier: r.identifier })}`), staleTime: 300000 })
  const v = sc.data ? VERDICT[sc.data.verdict] ?? { label: sc.data.verdict, cls: 'border-line text-muted' } : null
  return (
    <Modal title={r.name} onClose={onClose}>
      <div className="flex flex-wrap items-center gap-1 font-mono text-[10px]"><span className={clsx('rounded-full border px-1.5 py-0.5', TRUST[r.trust_level] ?? 'border-line text-muted')}>{r.trust_level}</span><span className="rounded-full border border-line px-1.5 py-0.5 text-muted">{r.source}</span>{sc.isLoading ? <span className="flex items-center gap-1 text-muted"><Spinner className="size-3" /> scanning…</span> : v && <span className={clsx('rounded-full border px-1.5 py-0.5', v.cls)} title={sc.data?.summary}>{v.label} · {sc.data?.policy}</span>}<span className="truncate text-muted">{r.identifier}</span></div>
      <div role="tablist" className="mt-2 flex gap-1 font-mono text-[10px] uppercase tracking-wider">{(['readme', 'scan'] as const).map(t => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={clsx('rounded-full px-2.5 py-1', tab === t ? 'bg-accent/20 text-fg' : 'text-muted hover:text-fg')}>{t}</button>)}</div>
      <div className="mt-2 max-h-[50dvh] min-h-[10rem] overflow-auto rounded-lg border border-line bg-inset p-3 text-sm">
        {tab === 'readme' ? (pv.isLoading ? <Skeleton rows={6} /> : pv.isError ? <Empty title="Could not fetch" note={(pv.error as Error).message} error /> : <><Markdown text={pv.data!.skill_md || `# ${r.name}\n\n${r.description}`} />{pv.data!.files.length > 1 && <p className="mt-3 font-mono text-[10px] text-muted">files: {pv.data!.files.join(', ')}</p>}</>)
          : sc.isLoading ? <Skeleton rows={4} /> : sc.isError ? <Empty title="Scan failed" note={(sc.error as Error).message} error /> : (
            <div className="text-xs">
              <p><span className={clsx('rounded-full border px-1.5 py-0.5 font-mono text-[10px]', v!.cls)}>{v!.label}</span> <span className="ml-1 text-muted">{sc.data!.summary}</span></p>
              <p className="mt-2 font-mono text-[10px] text-muted">policy {sc.data!.policy} — {sc.data!.policy_reason} · {Object.entries(sc.data!.severity_counts).map(([k, n]) => `${k} ${n}`).join(' · ')}</p>
              {sc.data!.findings.length === 0 ? <p className="mt-2 text-muted">No findings.</p> : <ul className="mt-2 grid gap-1">{sc.data!.findings.map((f, i) => <li key={i} className="rounded-lg border border-line p-2"><span className={clsx('font-mono text-[10px] uppercase', f.severity === 'critical' || f.severity === 'high' ? 'text-error' : f.severity === 'medium' ? 'text-needsyou' : 'text-muted')}>{f.severity}</span> <span className="font-mono text-[10px] text-muted">{f.category} · {f.file}{f.line ? `:${f.line}` : ''}</span><p>{f.description}</p></li>)}</ul>}
            </div>)}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Btn kind="ghost" onClick={onClose}>Close</Btn>
        {!installed && <Btn disabled={sc.isLoading || sc.data?.policy === 'block'} title={sc.data?.policy === 'block' ? sc.data.policy_reason : undefined} onClick={onInstall}>{sc.data?.policy === 'block' ? 'Blocked by scan' : sc.data?.policy === 'ask' ? 'Install anyway' : 'Install'}</Btn>}
      </div>
    </Modal>
  )
}
