import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, ago, type AgentSummary, type AgentTemplate } from '../api'
import { GlassCard, PageHeader } from '../components/GlassCard'
import { Empty, Loading, Chip } from '../components/ui'
import { ActionBtn } from '../components/forms'
import { Btn, Modal } from '../components/Modal'
import { usePageTitle } from '../usePageTitle'

export function GatewayDot({ g }: { g: AgentSummary['gateway'] }) {
  const label = g.running ? 'chat ready' : g.enabled ? 'chat starting' : g.configured ? 'chat off' : 'chat off'
  const cls = g.running ? 'bg-working' : g.enabled ? 'bg-queued' : 'bg-muted'
  const title = g.port ? `Hermes gateway on port ${g.port}${g.running ? ' (running)' : g.enabled ? ' (enabled, not answering yet)' : ' (disabled)'}` : 'No gateway configured — enable chat to set one up'
  return <span title={title} className="inline-flex items-center gap-1.5 font-mono text-[10px] text-muted"><span className={`size-1.5 rounded-full ${cls}`} />{label}</span>
}

function AgentCard({ a }: { a: AgentSummary }) {
  const accent = a.runs_running > 0 || a.active_now ? 'var(--hq-working)' : a.installed ? 'var(--hq-accent)' : 'var(--hq-muted)'
  return (
    <Link to={`/agents/${a.name}`} className="block">
      <GlassCard accent={accent} className="h-full transition hover:bg-raised">
        <div className="flex items-start justify-between gap-2">
          <h2 className="font-mono text-sm font-semibold text-accent-2">{a.name}</h2>
          {a.runs_running > 0 ? <Chip tone="accent">{a.runs_running} running</Chip> : !a.installed ? <Chip>not installed</Chip> : a.name === 'orchestrator' ? <Chip tone={a.overlay_applied ? 'accent' : 'muted'}>{a.overlay_applied ? 'HQ soul' : 'stock soul'}</Chip> : null}
        </div>
        <p className="mt-2 line-clamp-2 min-h-[2rem] text-xs text-muted">{a.description || (a.installed ? '—' : 'Install from template to add this agent.')}</p>
        {a.live.length > 0 && <p className="mt-2 truncate text-xs text-working"><span className="hq-dot-live mr-1.5 inline-block size-1.5 rounded-full bg-current" />run #{a.live[0].run_id}{a.live[0].task_id ? ` · #${a.live[0].task_id} ${a.live[0].task_title ?? ''}` : ''} · {ago(a.live[0].started_at)}</p>}
        <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-muted">
          <span>{a.runs} runs</span><span>{a.runs_done} done</span><span>{a.runs_failed} failed</span><span>{a.sessions} sessions</span>
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <GatewayDot g={a.gateway} />
          {a.last_active_at && <span className="text-[11px] text-muted">active {ago(a.last_active_at)}</span>}
        </div>
      </GlassCard>
    </Link>
  )
}

function AddFromTemplate({ templates, onClose }: { templates: AgentTemplate[]; onClose: () => void }) {
  const avail = templates.filter(t => !t.overlay)
  return (
    <Modal title="Add agent from template" onClose={onClose}>
      <p className="mb-3 text-xs text-muted">Creates the Hermes profile with <code className="font-mono">hermes profile create</code>, then layers the template SOUL and specialist skill on top.</p>
      <div className="flex flex-col gap-2">
        {avail.map(t => (
          <div key={t.name} className="flex items-center justify-between gap-3 rounded-lg border border-line p-3">
            <div className="min-w-0">
              <p className="font-mono text-sm text-accent-2">{t.name}</p>
              <p className="line-clamp-2 text-xs text-muted">{t.description}</p>
            </div>
            {t.installed ? <Chip>installed</Chip> : <ActionBtn url="/api/agents/install" label="Install" confirm={`Create the Hermes profile "${t.name}" and install the ${t.name} template?`} body={{ template: t.name }} onDone={onClose} />}
          </div>
        ))}
      </div>
    </Modal>
  )
}

export function Agents() {
  usePageTitle('Agents')
  const q = useAgents()
  const [adding, setAdding] = useState(false)
  const orch = q.data?.agents.find(a => a.name === 'orchestrator')
  const orchTemplate = q.data?.templates.find(t => t.name === 'orchestrator')
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <PageHeader crumb="agents" title="Agents" right={<div className="flex flex-wrap items-center gap-2">
        {orchTemplate && orch && !orch.overlay_applied && <ActionBtn url="/api/agents/install" label="Apply Orchestrator soul to default" kind="ghost" body={{ template: 'orchestrator' }} confirm="Overwrite the default profile's SOUL.md with the HQ Orchestrator soul? The current file is backed up next to it." />}
        <Btn onClick={() => setAdding(true)}>+ Agent</Btn></div>} />
      {adding && q.data && <AddFromTemplate templates={q.data.templates} onClose={() => setAdding(false)} />}
      {q.isLoading && <Loading rows={3} card />}
      {q.isError && <Empty error title="Could not load /api/agents" note={String(q.error)} />}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {q.data?.agents.map(a => <AgentCard key={a.name} a={a} />)}
      </div>
    </section>
  )
}
