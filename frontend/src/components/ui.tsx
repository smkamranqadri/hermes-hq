import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function Empty({ title, note, error }: { title: string; note?: string; error?: boolean }) {
  return (
    <div className={clsx('glass rounded-xl p-6 text-center', error && 'border-error/40')}>
      <p className={clsx('text-sm font-medium', error && 'text-error')}>{error ? '! ' : ''}{title}</p>
      {note && <p className="mt-1 text-xs text-muted">{note}</p>}
    </div>
  )
}
export function Loading() {
  return <div className="glass animate-pulse rounded-xl p-6 text-xs text-muted">Loading…</div>
}
export function Chip({ children, tone }: { children: ReactNode; tone?: 'accent' | 'muted' }) {
  return <span className={clsx('inline-flex items-center rounded-full border border-line bg-inset px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider', tone === 'accent' ? 'text-accent-2' : 'text-muted')}>{children}</span>
}
export function Label({ children }: { children: ReactNode }) {
  return <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">{children}</p>
}
export function Crumbs({ items }: { items: [string, string?][] }) {
  return (
    <nav className="mb-1 flex flex-wrap items-center gap-1 font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
      {items.map(([label, to], i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <span className="opacity-50">/</span>}
          {to ? <Link to={to} className="hover:text-fg">{label}</Link> : <span className="text-fg/80">{label}</span>}
        </span>
      ))}
    </nav>
  )
}
export function Agent({ name }: { name?: string | null }) {
  if (!name) return <span className="text-muted">unassigned</span>
  return <span className="font-mono text-xs text-accent-2">{name}</span>
}
export const Select = (p: React.SelectHTMLAttributes<HTMLSelectElement>) => (
  <select {...p} className={clsx('rounded-full border border-line bg-glass px-3 py-1.5 text-xs text-fg outline-none focus:border-accent', p.className)} />
)
export const Input = (p: React.InputHTMLAttributes<HTMLInputElement>) => (
  <input {...p} className={clsx('rounded-full border border-line bg-glass px-3 py-1.5 text-xs text-fg outline-none placeholder:text-muted focus:border-accent', p.className)} />
)
