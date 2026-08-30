import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { get, post } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Skeleton, Chip, Spinner } from '../components/ui'
import { Modal, Field, TextInput, TextArea, Btn } from '../components/Modal'
import { useToast } from '../components/Toast'
import { usePageTitle } from '../usePageTitle'
import { AgentSwitcher } from '../components/AgentSwitcher'
import { ActionBtn } from '../components/forms'

// Group 6-4 — MCP servers (Hermes config.yaml `mcp_servers`), the Nous MCP catalog, and the gateway's toolsets.
type Test = { ok: boolean; error?: string | null; tools: { name: string; description: string }[]; prompts: number; resources: number; ts: number }
type Server = { name: string; transport: 'http' | 'stdio' | 'unknown'; url: string | null; command: string | null; args: string[]; env: Record<string, string>; auth: string | null; enabled: boolean; tools: string[] | null; last_test: Test | null; has_token: boolean; login_command: string | null }
type Entry = { name: string; description: string; source: string; transport: string; auth_type: string; required_env: { name: string; prompt: string; required: boolean }[]; command: string | null; args: string[]; url: string | null; install_url: string | null; install_ref: string | null; bootstrap: string[]; post_install: string; needs_install: boolean; installed: boolean; enabled: boolean }
type Toolset = { name: string; label: string; description: string; enabled: boolean; configured: boolean; tools: string[] }
type Job = { id: string; label: string; status: 'running' | 'done' | 'failed'; log: string }

const q = (o: Record<string, string | number | undefined>) => Object.entries(o).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')
const TABS = ['servers', 'catalog', 'toolsets'] as const
type Tab = (typeof TABS)[number]
const pill = (cls: string) => clsx('rounded-full border px-1.5 py-0.5 font-mono text-[10px]', cls)
const placeholderHint = (s: Server, err: string) => (/Connection closed|EACCES|ENOENT|exited|No such file/i.test(err) && s.args.some(a => /<[^>]+>|\/path\/to\//.test(a))) || (/fetch failed|ENOTFOUND|network/i.test(err) && /example\.com|<[^>]+>/.test(s.url ?? '')) ? 'The command/url looks like a placeholder — edit the server.' : null

export function Mcp() {
  usePageTitle('MCP')
  const [params, setParamsRaw] = useSearchParams()
  const profile = params.get('profile') || 'orchestrator'
  const tab = (TABS as readonly string[]).includes(params.get('tab') || '') ? (params.get('tab') as Tab) : 'servers'
  const setParams = (patch: Record<string, string>) => setParamsRaw(p => { const n = new URLSearchParams(p); for (const [k, v] of Object.entries(patch)) { if (v) n.set(k, v); else n.delete(k) } return n }, { replace: true })
  return (
    <section className="mx-auto max-w-7xl p-4 sm:p-6">
      <PageHeader crumb="mcp" title="MCP" right={
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <AgentSwitcher value={profile} onChange={p => setParams({ profile: p })} className="w-full sm:w-40" />
          <div role="tablist" className="flex rounded-full border border-line bg-glass p-0.5 font-mono text-[10px] uppercase tracking-wider">
            {TABS.map(t => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setParams({ tab: t })} className={clsx('rounded-full px-2.5 py-1', tab === t ? 'bg-accent/20 text-fg' : 'text-muted hover:text-fg')}>{t}</button>)}
          </div>
        </div>} />
      {tab === 'servers' && <Servers profile={profile} />}
      {tab === 'catalog' && <Catalog profile={profile} />}
      {tab === 'toolsets' && <Toolsets profile={profile} />}
    </section>
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
const JobCard = ({ job, onClose }: { job: Job; onClose: () => void }) => (
  <GlassCard className="mb-3 py-3"><div className="flex items-center gap-2 text-xs"><span className={pill(job.status === 'running' ? 'border-accent/50 text-accent-2' : job.status === 'done' ? 'border-working/50 text-working' : 'border-error/50 text-error')}>{job.status}</span><span className="font-medium">{job.label}</span>{job.status === 'running' && <Spinner className="size-3" />}<button onClick={onClose} className="ml-auto font-mono text-[10px] text-muted hover:text-fg">dismiss</button></div>
    {job.log && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-inset p-2 font-mono text-[11px] text-muted">{job.log.replace(/\x1b\[[0-9;]*m/g, '')}</pre>}</GlassCard>)

// ---------------------------------------------------------------- Servers
function Servers({ profile }: { profile: string }) {
  const qc = useQueryClient(); const toast = useToast()
  const list = useQuery({ queryKey: ['mcp', profile], queryFn: () => get<{ servers: Server[] }>(`/api/mcp?${q({ profile })}`), staleTime: 30000 })
  const [adding, setAdding] = useState(false); const [busy, setBusy] = useState<string | null>(null); const [confirm, setConfirm] = useState<string | null>(null); const [hint, setHint] = useState(false)
  const refresh = () => qc.invalidateQueries({ queryKey: ['mcp', profile] })
  const act = async (key: string, fn: () => Promise<unknown>, msg?: string) => { setBusy(key); try { await fn(); if (msg) toast(msg); await get(`/api/mcp?${q({ profile, fresh: 1 })}`); refresh() } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(null) } }
  const servers = list.data?.servers ?? []
  return (
    <div className="min-w-0">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[10px] uppercase tracking-widest text-muted">{servers.length} server{servers.length === 1 ? '' : 's'} in {profile}'s config.yaml{hint && <span className="ml-2 normal-case tracking-normal text-needsyou">· changes apply on the next session / gateway restart</span>}</p>
        <Btn onClick={() => setAdding(true)}>Add server</Btn>
      </div>
      {list.isLoading ? <div className="grid gap-3 lg:grid-cols-2"><Skeleton rows={4} card /><Skeleton rows={4} card /></div>
        : list.isError ? <GlassCard><Empty title="Could not read servers" note={(list.error as Error).message} error /></GlassCard>
        : servers.length === 0 ? <GlassCard><Empty title="No MCP servers configured" note={`Add one here, pick one from the catalog, or run \`hermes --profile ${profile === 'orchestrator' ? 'default' : profile} mcp add <name> --url … | --command …\` on the host.`} /></GlassCard>
        : <div className="grid gap-3 lg:grid-cols-2">
          {servers.map(s => {
            const t = s.last_test; const err = t && !t.ok ? (t.error ?? 'failed') : null
            return (
              <GlassCard key={s.name} className={clsx('flex min-w-0 flex-col gap-2', !s.enabled && 'opacity-70')}>
                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  <span className="truncate text-sm font-semibold">{s.name}</span>
                  <span className={pill(t ? (t.ok ? 'border-working/50 text-working' : 'border-error/50 text-error') : 'border-line text-muted')}>{t ? (t.ok ? 'connected' : 'failed') : 'untested'}</span>
                  <span className={pill('border-line text-muted')}>{s.transport}</span>
                  {s.auth && <span className={pill('border-accent/50 text-accent-2')}>{s.auth === 'header' ? 'bearer' : s.auth}</span>}
                  {!s.enabled && <span className={pill('border-needsyou/50 text-needsyou')}>disabled</span>}
                  <button role="switch" aria-checked={s.enabled} aria-label={`${s.enabled ? 'Disable' : 'Enable'} ${s.name}`} disabled={busy === s.name} onClick={() => act(s.name, () => post('/api/mcp/toggle', { profile, name: s.name, enabled: !s.enabled }), `${s.name} ${s.enabled ? 'disabled' : 'enabled'}`).then(() => setHint(true))} className={clsx('relative ml-auto h-5 w-9 shrink-0 rounded-full border transition', s.enabled ? 'border-accent bg-accent/60' : 'border-line bg-inset')}><span className={clsx('absolute top-0.5 size-3.5 rounded-full bg-fg transition', s.enabled ? 'left-4' : 'left-0.5')} /></button>
                </div>
                <p className="truncate font-mono text-[11px] text-muted" title={s.url ?? `${s.command} ${s.args.join(' ')}`}>{s.url ?? `${s.command} ${s.args.join(' ')}`}</p>
                <dl className="grid grid-cols-3 gap-2 font-mono text-[10px] text-muted">
                  <div><dt className="uppercase tracking-widest">tools</dt><dd className="text-fg">{t?.ok ? t.tools.length : s.tools ? `${s.tools.length} allowed` : '—'}</dd></div>
                  <div><dt className="uppercase tracking-widest">auth</dt><dd className="text-fg">{s.auth === 'header' ? (s.has_token ? 'bearer (in .env)' : 'bearer') : s.auth ?? 'none'}</dd></div>
                  <div><dt className="uppercase tracking-widest">env</dt><dd className="text-fg">{Object.keys(s.env).length ? Object.keys(s.env).join(', ') : '—'}</dd></div>
                </dl>
                {s.tools && <div className="flex flex-wrap gap-1">{s.tools.map(x => <Chip key={x} tone="muted">{x}</Chip>)}</div>}
                {t?.ok && <details className="text-xs"><summary className="cursor-pointer font-mono text-[10px] text-muted">{t.tools.length} tools · {t.prompts} prompts · {t.resources} resources</summary><ul className="mt-1 grid gap-1">{t.tools.map(x => <li key={x.name}><span className="font-mono text-accent-2">{x.name}</span> <span className="text-muted">{x.description}</span></li>)}</ul></details>}
                {err && <p className="rounded-lg border border-error/40 bg-error/10 p-2 text-xs text-error">{err}{placeholderHint(s, err) && <span className="block text-needsyou">{placeholderHint(s, err)}</span>}</p>}
                {s.login_command && <p className="rounded-lg border border-line bg-inset p-2 text-[11px]"><span className="text-muted">OAuth — authenticate on the host, then Test:</span> <code className="select-all font-mono">{s.login_command}</code> <button onClick={() => { navigator.clipboard?.writeText(s.login_command!); toast('Copied') }} className="ml-1 font-mono text-[10px] text-accent-2">copy</button></p>}
                <div className="mt-auto flex flex-wrap justify-end gap-2">
                  <Btn kind="ghost" busy={busy === 'test:' + s.name} onClick={() => act('test:' + s.name, async () => { const r = await post<Test>('/api/mcp/test', { profile, name: s.name }); if (!r.ok) throw new Error(r.error ?? 'test failed') }, 'Connected')}>Test</Btn>
                  {confirm === s.name
                    ? <><Btn kind="ghost" onClick={() => setConfirm(null)}>Cancel</Btn><Btn kind="warn" busy={busy === 'rm:' + s.name} onClick={() => act('rm:' + s.name, () => post('/api/mcp/remove', { profile, name: s.name }), `${s.name} removed`).then(() => setConfirm(null))}>Confirm delete</Btn></>
                    : <Btn kind="ghost" onClick={() => setConfirm(s.name)}>Delete</Btn>}
                </div>
              </GlassCard>)
          })}
        </div>}
      {adding && <AddModal profile={profile} onClose={() => setAdding(false)} onAdded={() => { setAdding(false); setHint(true); void get(`/api/mcp?${q({ profile, fresh: 1 })}`).then(refresh) }} />}
    </div>
  )
}

function AddModal({ profile, onClose, onAdded }: { profile: string; onClose: () => void; onAdded: () => void }) {
  const toast = useToast()
  const [f, setF] = useState({ name: '', transport: 'http', url: '', command: '', args: '', env: '', auth: 'none', bearer_token: '' })
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF(x => ({ ...x, [k]: e.target.value }))
  const submit = async () => {
    setBusy(true)
    try {
      const env = Object.fromEntries(f.env.split('\n').map(l => l.trim()).filter(Boolean).map(l => { const i = l.indexOf('='); return i > 0 ? [l.slice(0, i).trim(), l.slice(i + 1)] : [l, ''] }))
      await post('/api/mcp/add', { profile, name: f.name.trim(), transport: f.transport, url: f.url.trim() || undefined, command: f.command.trim() || undefined, args: f.args.split(/[\s,]+/).filter(Boolean), env, auth: f.transport === 'http' ? f.auth : 'none', bearer_token: f.auth === 'bearer' ? f.bearer_token : undefined })
      toast(f.auth === 'oauth' ? `${f.name} added — authenticate with the login command, then Test` : `${f.name} added`); onAdded()
    } catch (e) { toast(e instanceof Error ? e.message : String(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title="Add MCP server" onClose={onClose}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><TextInput value={f.name} onChange={set('name')} placeholder="my-server" /></Field>
        <Field label="Transport"><select value={f.transport} onChange={set('transport')} className="hq-select w-full appearance-none rounded-lg border border-line bg-inset px-3 py-2 text-sm outline-none focus:border-accent"><option value="http">HTTP / SSE</option><option value="stdio">stdio (command)</option></select></Field>
        {f.transport === 'http' ? <>
          <Field label="URL"><TextInput value={f.url} onChange={set('url')} placeholder="https://mcp.example.com/sse" /></Field>
          <Field label="Authentication"><select value={f.auth} onChange={set('auth')} className="hq-select w-full appearance-none rounded-lg border border-line bg-inset px-3 py-2 text-sm outline-none focus:border-accent"><option value="none">None</option><option value="bearer">Bearer token</option><option value="oauth">OAuth</option></select></Field>
          {f.auth === 'bearer' && <Field label="Bearer token" hint="stored in this profile's .env; config.yaml keeps only an environment-variable reference"><TextInput type="password" value={f.bearer_token} onChange={set('bearer_token')} autoComplete="off" /></Field>}
          {f.auth === 'oauth' && <p className="text-[11px] text-muted sm:col-span-2">Add first, then run the login command shown on the card on the host (the OAuth browser opens there), then Test.</p>}
        </> : <>
          <Field label="Command"><TextInput value={f.command} onChange={set('command')} placeholder="npx" /></Field>
          <Field label="Arguments" hint="space or comma separated"><TextInput value={f.args} onChange={set('args')} placeholder="-y @modelcontextprotocol/server-filesystem /opt/data" /></Field>
          <div className="sm:col-span-2"><Field label="Environment" hint="KEY=VALUE per line"><TextArea value={f.env} onChange={set('env')} placeholder={'API_KEY=…'} /></Field></div>
        </>}
      </div>
      <div className="mt-3 flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn busy={busy} disabled={!f.name.trim() || (f.transport === 'http' ? !f.url.trim() : !f.command.trim()) || (f.auth === 'bearer' && !f.bearer_token)} onClick={submit}>Add</Btn></div>
    </Modal>
  )
}

// ---------------------------------------------------------------- Catalog
function Catalog({ profile }: { profile: string }) {
  const qc = useQueryClient(); const toast = useToast()
  const cat = useQuery({ queryKey: ['mcp-catalog', profile], queryFn: () => get<{ entries: Entry[]; diagnostics: { name: string; kind: string; message: string }[] }>(`/api/mcp/catalog?${q({ profile })}`), staleTime: 120000 })
  const [search, setSearch] = useState(''); const [prompt, setPrompt] = useState<Entry | null>(null); const [job, setJob] = useState<Job | null>(null); const [busy, setBusy] = useState<string | null>(null)
  const done = () => { void get(`/api/mcp/catalog?${q({ profile, fresh: 1 })}`).then(() => qc.invalidateQueries({ queryKey: ['mcp-catalog', profile] })); void get(`/api/mcp?${q({ profile, fresh: 1 })}`).then(() => qc.invalidateQueries({ queryKey: ['mcp', profile] })) }
  useJob(job, setJob, done)
  const install = async (e: Entry, env: Record<string, string> = {}) => {
    setBusy(e.name)
    try { const r = await post<{ background: boolean; job?: Job }>('/api/mcp/catalog/install', { profile, name: e.name, env, enable: true }); if (r.background && r.job) { setJob({ ...r.job, log: '' }); toast(`Installing ${e.name} in the background…`) } else { toast(`${e.name} installed — restart the gateway to use it`); done() } }
    catch (err) { toast(err instanceof Error ? err.message : String(err), 'err') } finally { setBusy(null); setPrompt(null) }
  }
  const entries = (cat.data?.entries ?? []).filter(e => !search || `${e.name} ${e.description}`.toLowerCase().includes(search.toLowerCase()))
  return (
    <div className="min-w-0">
      <div className="mb-3 flex flex-wrap items-center gap-2"><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Filter the Nous catalog" aria-label="Filter catalog" className="min-w-0 flex-1 rounded-lg border border-line bg-inset px-3 py-1.5 text-sm outline-none placeholder:text-muted focus:border-accent" /><span className="font-mono text-[10px] uppercase tracking-widest text-muted">{entries.length} of {cat.data?.entries.length ?? '…'}</span></div>
      {job && <JobCard job={job} onClose={() => setJob(null)} />}
      {cat.data?.diagnostics.map((d, i) => <p key={i} className="mb-2 text-xs text-needsyou">{d.name}: {d.message}</p>)}
      {cat.isLoading ? <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} rows={3} card />)}</div>
        : cat.isError ? <GlassCard><Empty title="Catalog unavailable" note={(cat.error as Error).message} error /></GlassCard>
        : <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {entries.map(e => (
            <GlassCard key={e.name} className="flex min-w-0 flex-col gap-2 py-3">
              <div className="flex flex-wrap items-center gap-1"><span className="text-sm font-semibold">{e.name}</span><span className={pill('border-line text-muted')}>{e.transport}</span><span className={pill('border-accent/50 text-accent-2')}>auth: {e.auth_type}</span>{e.installed && <Chip>installed</Chip>}{e.installed && !e.enabled && <span className={pill('border-needsyou/50 text-needsyou')}>disabled</span>}</div>
              <p className="line-clamp-2 text-xs text-muted">{e.description}</p>
              <details className="text-[11px] text-muted"><summary className="cursor-pointer font-mono text-[10px]">details</summary>
                <p className="mt-1 break-all font-mono">{e.url ? `Endpoint: ${e.url}` : `Runs: ${e.command} ${e.args.join(' ')}`}</p>
                {e.install_url && <p className="break-all font-mono">Installs from: {e.install_url}{e.install_ref ? ` @ ${e.install_ref}` : ''}</p>}
                {e.bootstrap.length > 0 && <p className="mt-1">Bootstrap: <span className="font-mono">{e.bootstrap.join(' && ')}</span></p>}
                {e.required_env.length > 0 && <p className="mt-1">Needs env: {e.required_env.map(v => v.name).join(', ')}</p>}
                {e.post_install && <p className="mt-1">{e.post_install}</p>}
                {e.source && <a href={e.source} target="_blank" rel="noreferrer" className="mt-1 block text-accent-2 hover:underline">source ↗</a>}
              </details>
              <div className="mt-auto flex justify-end">{e.installed ? <span className="font-mono text-[10px] text-muted">already in config</span> : <Btn kind="ghost" busy={busy === e.name} onClick={() => e.required_env.length ? setPrompt(e) : install(e)}>Install</Btn>}</div>
            </GlassCard>))}
        </div>}
      {prompt && <EnvModal entry={prompt} onClose={() => setPrompt(null)} onSubmit={env => install(prompt, env)} />}
    </div>
  )
}

function EnvModal({ entry, onClose, onSubmit }: { entry: Entry; onClose: () => void; onSubmit: (env: Record<string, string>) => void }) {
  const [v, setV] = useState<Record<string, string>>({})
  const missing = entry.required_env.some(e => e.required && !v[e.name]?.trim())
  return (
    <Modal title={`Install ${entry.name}`} onClose={onClose}>
      <p className="text-xs text-muted">Values go to this profile's <span className="font-mono">.env</span>; config.yaml keeps only the variable names.</p>
      <div className="mt-3 grid gap-3">{entry.required_env.map(e => <Field key={e.name} label={`${e.name}${e.required ? ' *' : ''}`} hint={e.prompt}><TextInput type="password" value={v[e.name] ?? ''} onChange={ev => setV(x => ({ ...x, [e.name]: ev.target.value }))} autoComplete="off" /></Field>)}</div>
      <div className="mt-3 flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn disabled={missing} onClick={() => onSubmit(Object.fromEntries(Object.entries(v).filter(([, x]) => x.trim())))}>Install</Btn></div>
    </Modal>
  )
}

// ---------------------------------------------------------------- Toolsets
function Toolsets({ profile }: { profile: string }) {
  const qc = useQueryClient()
  const ts = useQuery({ queryKey: ['toolsets', profile], queryFn: () => get<{ gateway: 'on' | 'off'; platform?: string; toolsets: Toolset[] }>(`/api/mcp/toolsets?${q({ profile })}`), staleTime: 30000 })
  if (ts.isLoading) return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} rows={3} card />)}</div>
  if (ts.isError) return <GlassCard><Empty title="Could not read toolsets" note={(ts.error as Error).message} error /></GlassCard>
  if (ts.data!.gateway === 'off') return (
    <GlassCard><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold">{profile}'s gateway is off</p><p className="mt-1 text-xs text-muted">Toolsets are what the running gateway reports (`/v1/toolsets`); turn it on to see them.</p></div>
      {profile !== 'orchestrator' && <ActionBtn url={`/api/agent/${profile}/gateway`} body={{ enabled: true }} label="Turn gateway on" onDone={() => setTimeout(() => qc.invalidateQueries({ queryKey: ['toolsets', profile] }), 3000)} />}</div></GlassCard>)
  const on = ts.data!.toolsets.filter(t => t.enabled), off = ts.data!.toolsets.filter(t => !t.enabled)
  const card = (t: Toolset) => (
    <GlassCard key={t.name} className={clsx('flex min-w-0 flex-col gap-2 py-3', !t.enabled && 'opacity-70')}>
      <div className="flex flex-wrap items-center gap-1"><span className="text-sm font-semibold">{t.label || t.name}</span><span className={pill(t.enabled ? 'border-working/50 text-working' : 'border-line text-muted')}>{t.enabled ? 'enabled' : 'off'}</span>{!t.configured && <span className={pill('border-needsyou/50 text-needsyou')}>setup needed</span>}</div>
      {t.description && <p className="text-xs text-muted">{t.description}</p>}
      <div className="flex flex-wrap gap-1">{t.tools.map(x => <Chip key={x} tone="muted">{x}</Chip>)}{t.tools.length === 0 && <span className="font-mono text-[10px] text-muted">no tools</span>}</div>
    </GlassCard>)
  return (
    <div className="min-w-0">
      <p className="mb-2 font-mono text-[10px] uppercase tracking-widest text-muted">{on.length} enabled · {off.length} off · platform {ts.data!.platform ?? '—'} · read-only (Hermes' own dashboard configures keys)</p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{on.map(card)}{off.map(card)}</div>
    </div>
  )
}
