import { useEffect, useRef, useState, type ReactNode } from 'react'

// Minimal click-outside dropdown used by Tools and the theme picker.
export function Menu({ button, children, align = 'right' }: { button: ReactNode; children: ReactNode; align?: 'left' | 'right' }) {
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
        className="rounded-md px-2 py-1 text-xs text-muted hover:bg-raised hover:text-fg">{button}</button>
      {open && (
        <div onClick={() => setOpen(false)}
          className={`glass-strong absolute top-full mt-2 min-w-44 rounded-lg p-1 shadow-[0_16px_36px_rgba(0,0,0,0.4)] ${align === 'right' ? 'right-0' : 'left-0'}`}>
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
