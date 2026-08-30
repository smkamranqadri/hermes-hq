import { useEffect, useState } from 'react'
import clsx from 'clsx'

/** One tool call: name + preview row; expand for arguments and result. Live cards tick an elapsed timer. */
export type ToolView = { key: string; name: string; state: 'started' | 'completed' | 'failed'; preview?: string; args?: string; result?: string | null; startedAt?: number; endedAt?: number | null }

function fmtArgs(a?: string) {
  if (!a) return ''
  try { return JSON.stringify(JSON.parse(a), null, 2) } catch { return a }
}

export function ToolCard({ t }: { t: ToolView }) {
  const [open, setOpen] = useState(false)
  const [now, setNow] = useState(Date.now())
  useEffect(() => { if (t.state !== 'started') return; const i = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(i) }, [t.state])
  const secs = t.startedAt ? Math.max(0, ((t.endedAt ?? now / 1000) - t.startedAt)) : null
  const tone = t.state === 'failed' ? 'text-needsyou' : t.state === 'started' ? 'text-queued' : 'text-accent-2'
  return (
    <div className="flex min-w-0 justify-start">
      <div className="min-w-0 max-w-[92%] rounded-xl border border-line bg-inset font-mono text-[11px] text-muted sm:max-w-[80%]">
        <button type="button" onClick={() => setOpen(o => !o)} className="flex w-full min-w-0 items-center gap-2 px-3 py-1.5 text-left hover:text-fg">
          <span className={clsx('shrink-0', tone)}>{t.state === 'started' ? '▸' : t.state === 'failed' ? '✕' : '✓'} {t.name}</span>
          <span className="min-w-0 flex-1 truncate">{t.preview ?? (t.args ? t.args.slice(0, 120) : '')}</span>
          {secs !== null && <span className="shrink-0 text-[10px]">{t.state === 'started' ? `${secs.toFixed(0)}s` : secs < 1 ? '<1s' : `${secs.toFixed(0)}s`}</span>}
          <span className="shrink-0 text-[10px]">{open ? '▾' : '▸'}</span>
        </button>
        {open && (
          <div className="border-t border-line px-3 py-2">
            {t.args && <><p className="text-[10px] uppercase tracking-wider">args</p><pre className="mb-2 max-h-60 overflow-auto whitespace-pre-wrap break-words">{fmtArgs(t.args)}</pre></>}
            {t.result != null && <><p className="text-[10px] uppercase tracking-wider">result</p><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words">{t.result}</pre></>}
            {t.result == null && t.state !== 'started' && <p className="italic">result not stored</p>}
          </div>
        )}
      </div>
    </div>
  )
}

/** Reasoning: collapsed one-liner while streaming, expandable afterwards. */
export function Thinking({ text, live }: { text: string; live?: boolean }) {
  const [open, setOpen] = useState(false)
  if (!text.trim()) return null
  return (
    <div className="flex min-w-0 justify-start">
      <div className="min-w-0 max-w-[92%] rounded-xl border border-dashed border-line px-3 py-1.5 font-mono text-[11px] text-muted sm:max-w-[80%]">
        <button type="button" onClick={() => setOpen(o => !o)} className="flex w-full items-center gap-2 text-left hover:text-fg">
          <span className={clsx('italic', live && 'animate-pulse')}>thinking{live ? '…' : ''}</span>
          {!open && <span className="min-w-0 flex-1 truncate">{text.slice(-140)}</span>}
          <span className="shrink-0 text-[10px]">{open ? '▾' : '▸'}</span>
        </button>
        {open && <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words">{text}</pre>}
      </div>
    </div>
  )
}

export function fmtTokens(n?: number | null) { return n == null ? '—' : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n) }
