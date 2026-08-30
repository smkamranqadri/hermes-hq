import { useEffect, type ReactNode } from 'react'

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', k); return () => document.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-6" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="glass-strong max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-t-2xl p-5 shadow-2xl sm:rounded-2xl">
        <div className="mb-4 flex items-center justify-between"><h2 className="text-base font-semibold">{title}</h2><button onClick={onClose} className="text-muted hover:text-fg">✕</button></div>
        {children}
      </div>
    </div>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="block"><span className="font-mono text-[10px] uppercase tracking-widest text-muted">{label}</span><div className="mt-1">{children}</div>{hint && <span className="text-[11px] text-muted">{hint}</span>}</label>
}
export const TextInput = (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} className="w-full rounded-lg border border-line bg-inset px-3 py-2 text-sm outline-none focus:border-accent" />
export const TextArea = (p: React.ComponentProps<'textarea'>) => <textarea {...p} className="w-full rounded-lg border border-line bg-inset px-3 py-2 text-sm outline-none focus:border-accent" rows={p.rows ?? 3} />
export const SelectInput = (p: React.SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className="w-full rounded-lg border border-line bg-inset px-3 py-2 text-sm outline-none focus:border-accent" />
export function Btn({ kind = 'primary', busy, children, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: 'primary' | 'ghost' | 'warn'; busy?: boolean }) {
  const cls = { primary: 'bg-accent text-white hover:opacity-90', ghost: 'border border-line text-muted hover:text-fg', warn: 'border border-needsyou/60 bg-needsyou/10 text-needsyou hover:bg-needsyou/20' }[kind]
  return <button {...p} disabled={p.disabled || busy} className={`inline-flex items-center justify-center gap-2 rounded-full px-4 py-1.5 font-mono text-[11px] uppercase tracking-wider disabled:opacity-60 ${cls} ${p.className ?? ''}`}>{busy && <span className="inline-block size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}{children}</button>
}
