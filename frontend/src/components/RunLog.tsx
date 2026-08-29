import { useEffect, useRef, useState } from 'react'
import { useRunLog } from '../api'
import { Label } from './ui'

/** Follow-tail of runs/<id>.log; polls while the run is active. */
export function RunLog({ runId, active }: { runId: number; active: boolean }) {
  const [buf, setBuf] = useState(''); const [offset, setOffset] = useState(0)
  const q = useRunLog(runId, offset, active)
  const ref = useRef<HTMLPreElement>(null)
  useEffect(() => {
    if (!q.data?.exists) return
    if (q.data.data) { setBuf(b => b + q.data!.data); setOffset(q.data.next) }
  }, [q.data])
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight }) }, [buf])
  if (q.data && !q.data.exists) return <p className="text-xs text-muted">No log file for run #{runId} yet.</p>
  return (
    <div>
      <div className="mb-1 flex items-center justify-between"><Label>Run #{runId} log{active ? ' · following' : ''}</Label><span className="font-mono text-[10px] text-muted">{q.data ? `${Math.round(q.data.size / 1024)} KB` : ''}</span></div>
      <pre ref={ref} className="max-h-96 overflow-auto rounded-xl border border-line bg-inset p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-fg/90">{buf || (q.isLoading ? '…' : '(empty)')}</pre>
    </div>
  )
}
