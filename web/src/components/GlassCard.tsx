import clsx from 'clsx'
import type { ReactNode } from 'react'

export function GlassCard({ children, className, accent }: { children: ReactNode; className?: string; accent?: string }) {
  return (
    <div className={clsx('glass rounded-xl p-4 shadow-[0_6px_16px_rgba(0,0,0,0.25)]', accent && 'border-l-[3px]', className)}
      style={accent ? { borderLeftColor: accent } : undefined}>
      {children}
    </div>
  )
}

export function PageHeader({ crumb, title, right }: { crumb: string; title: string; right?: ReactNode }) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">hermes-hq // {crumb}</p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">{title}</h1>
      </div>
      {right}
    </div>
  )
}
