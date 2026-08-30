import { useEffect, useRef, useState, type ReactNode } from 'react'

// Minimal click-outside dropdown used by Tools and the theme picker.
export function Menu({ button, children, align = 'right', keepOpen = false }: { button: ReactNode; children: ReactNode; align?: 'left' | 'right'; keepOpen?: boolean }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc); document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [open])
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="rounded-full border border-line bg-glass px-2.5 py-1 font-mono text-[10px] text-muted hover:text-fg">{button}</button>
      {open && (
        <div onClick={() => { if (!keepOpen) setOpen(false) }}
          className={`glass-strong hq-menu absolute top-full mt-2 min-w-44 rounded-xl p-1 shadow-[0_16px_36px_rgba(0,0,0,0.4)] ${align === 'right' ? 'right-0' : 'left-0'}`}>
          {children}
        </div>
      )}
    </div>
  )
}

export function MenuItem({ children, onClick, active }: { children: ReactNode; onClick?: () => void; active?: boolean }) {
  return (
    <button type="button" onClick={onClick}
      className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-raised ${active ? 'text-accent-2' : 'text-fg'}`}>
      {children}
    </button>
  )
}
