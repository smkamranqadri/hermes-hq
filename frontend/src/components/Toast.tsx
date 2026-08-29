import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import clsx from 'clsx'

type T = { id: number; text: string; kind: 'ok' | 'err' }
const Ctx = createContext<(text: string, kind?: 'ok' | 'err') => void>(() => {})
export const useToast = () => useContext(Ctx)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [list, setList] = useState<T[]>([])
  const push = useCallback((text: string, kind: 'ok' | 'err' = 'ok') => {
    const id = Date.now() + Math.random()
    setList(l => [...l, { id, text, kind }])
    setTimeout(() => setList(l => l.filter(t => t.id !== id)), kind === 'err' ? 8000 : 4000)
  }, [])
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4">
        {list.map(t => (
          <div key={t.id} className={clsx('glass-strong pointer-events-auto max-w-xl rounded-xl px-4 py-2 text-sm shadow-lg', t.kind === 'err' ? 'border-error/50 text-error' : 'border-working/40')}>
            {t.kind === 'ok' ? '✓ ' : '! '}{t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}
