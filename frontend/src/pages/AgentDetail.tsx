import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAgent, ago, when } from '../api'
import { GlassCard } from '../components/GlassCard'
import { StatusBadge } from '../components/StatusBadge'
import { Empty, Loading, Chip, Crumbs, Label } from '../components/ui'
import { ActionBtn } from '../components/forms'
import { Btn } from '../components/Modal'
import { usePageTitle } from '../usePageTitle'
import { GatewayDot } from './Agents'

export function AgentDetail() {
  const name = useParams().name ?? ''
  const [shown, setShown] = useState(30)
  const q = useAgent(name, Math.max(120, shown))   // Show more past 120 refetches a deeper history
  const a = q.data
  usePageTitle(a ? `Agent ${a.name}` : 'Agent')
  if (q.isLoading) return <section className="mx-auto max-w-6xl p-4 sm:p-6"><Loading rows={1} /><div className="mt-4 grid gap-4 lg:grid-cols-2"><Loading rows={3} /><Loading rows={3} /></div></section>
  if (q.isError || !a) return <section className="mx-auto max-w-6xl p-6"><Empty error title={`Could not load /api/agent/${name}`} note={String(q.error ?? '404')} /></section>
  const g = a.gateway
  const isDefault = a.name === 'orchestrator'
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <Crumbs items={[['Agents', '/agents'], [a.name]]} />
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-mono text-lg font-semibold text-accent-2 sm:text-xl">{a.name}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">{a.description}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
            {!a.installed && <Chip>not installed</Chip>}{isDefault && <Chip>default profile</Chip>}{isDefault && <Chip tone={a.overlay_applied ? 'accent' : 'muted'}>{a.overlay_applied ? 'HQ soul' : 'stock soul'}</Chip>}
            <span className="break-all font-mono text-[10px]">{a.home}</span>
          </div>
        </div>
        <div className="flex basis-full flex-wrap items-center gap-2">
          <GatewayDot g={g} />
          {!isDefault && a.installed && (g.enabled
            ? <ActionBtn url={`/api/agent/${a.name}/gateway`} label="Disable chat" kind="ghost" body={{ enabled: false }} confirm={`Stop the ${a.name} gateway? Open chats will drop.`} />
            : <ActionBtn url={`/api/agent/${a.name}/gateway`} label="Enable chat" body={{ enabled: true }} confirm={`Start the ${a.name} gateway on :${g.port ?? 'auto'}? Its .env gets API_SERVER_PORT/KEY if missing.`} />)}
          {isDefault && !a.overlay_applied && <ActionBtn url="/api/agents/install" label="Apply Orchestrator soul" kind="ghost" body={{ template: 'orchestrator' }} confirm="Overwrite the default profile's SOUL.md with the HQ Orchestrator soul? The current file is backed up next to it." />}
        </div>
      </div>
      <GlassCard className="min-w-0 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2"><Label>History · {a.runs} task runs · {a.sessions} sessions</Label><span className="font-mono text-[10px] text-muted">one row per Hermes session; task runs carry their run # and task</span></div>
        {a.live.length > 0 && <div className="mt-2 rounded-lg border border-working/50 bg-working/10 p-2">
          <Label>Running now · {a.live.length}</Label>
          {a.live.map(l => <div key={l.run_id} className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs"><span className="hq-dot-live size-1.5 rounded-full bg-working" /><span className="font-mono">run #{l.run_id}{l.review_id ? ' · review' : ''}</span>{l.task_id ? <Link to={`/tasks/${l.task_id}`} className="order-last min-w-0 basis-full truncate hover:underline sm:order-none sm:flex-1 sm:basis-auto">#{l.task_id} {l.task_title}</Link> : <span className="flex-1" />}<span className="text-muted">started {ago(l.started_at)}</span>{l.task_id && <Link to={`/tasks/${l.task_id}`} className="rounded-full border border-working/60 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-working hover:bg-working/20">Watch log</Link>}</div>)}
        </div>}
        {a.history.length === 0 && <p className="mt-2 text-xs text-muted">Nothing yet — no runs and no sessions in this profile's state.db.</p>}
        <ul className="mt-2 divide-y divide-line">
          {a.history.filter(h => !(h.run && h.run.status === 'running')).slice(0, shown).map((h, i) => {
            const s = h.session, r = h.run
            const title = s ? (s.title || s.id) : `run #${r?.id} (no session)`
            return (
              <li key={s?.id ?? `run-${r?.id ?? i}`} className="min-w-0 py-2 text-xs">
                <div className="flex min-w-0 items-center gap-2">
                  {r ? <StatusBadge status={r.status} compact /> : <Chip tone={h.kind === 'chat' ? 'accent' : 'muted'}>{h.kind}</Chip>}
                  <span className="min-w-0 flex-1 truncate">{s ? <Link to={`/chat/${a.name}/${s.id}`} className="block truncate hover:underline">{title}</Link> : title}</span>
                  <span className="shrink-0 text-muted">{ago(h.ts)}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[10px] text-muted">
                  {r && <span>run #{r.id}{r.review_id ? ' · review' : ''}</span>}
                  {s ? <><span title={s.id}>{s.id.slice(0, 14)}</span>{s.model && <span>{s.model}</span>}{s.message_count != null && <span>{s.message_count} msgs</span>}{s.estimated_cost_usd != null && <span>${s.estimated_cost_usd.toFixed(2)}</span>}
                    </>
                    : <span>no session{r?.error ? ` · ${r.error.slice(0, 80)}` : ''}</span>}
                  {r?.task_id && <Link to={`/tasks/${r.task_id}`} className="rounded-full border border-line px-2 py-0.5 uppercase tracking-wider text-fg hover:bg-raised" title={r.task_title ?? ''}>Task #{r.task_id}</Link>}
                </div>
              </li>
            )
          })}
        </ul>
        {(() => {
          const rows = a.history.filter(h => !(h.run && h.run.status === 'running'))
          const more = rows.length > shown || (a.history.length >= Math.max(120, shown) && shown < 1000)
          return more && <div className="mt-3 text-center"><Btn kind="ghost" busy={q.isFetching} onClick={() => setShown(s => s + 40)} data-history-more>Show more</Btn></div>
        })()}
        {a.last_active_at && <p className="mt-2 text-[11px] text-muted">last active {when(a.last_active_at)}</p>}
      </GlassCard>
    </section>
  )
}
