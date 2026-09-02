import { useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* try the legacy fallback below */ }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  area.setSelectionRange(0, area.value.length)
  try { return document.execCommand('copy') } catch { return false } finally { area.remove() }
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return <button type="button" onClick={() => { void copyText(text).then(copied => { if (copied) { setOk(true); setTimeout(() => setOk(false), 1200) } }) }}
    className="absolute right-1.5 top-1.5 rounded-md border border-line bg-glass px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted opacity-0 transition hover:text-fg group-hover/code:opacity-100 focus:opacity-100">{ok ? 'copied' : 'copy'}</button>
}

function textOf(n: ReactNode): string {
  if (typeof n === 'string' || typeof n === 'number') return String(n)
  if (Array.isArray(n)) return n.map(textOf).join('')
  if (n && typeof n === 'object' && 'props' in n) return textOf((n as { props: { children?: ReactNode } }).props.children)
  return ''
}

/** Agent-asked question with clickable options: a ```hq-options fenced block carrying JSON. */
export type OptionBlock = { question?: string; mode?: 'single' | 'multi'; options: { label: string; detail?: string }[] }
export function parseOptions(raw: string): OptionBlock | null {
  try {
    const o = JSON.parse(raw) as OptionBlock
    if (!o || !Array.isArray(o.options) || o.options.length < 1) return null
    o.options = o.options.filter(x => x && typeof x.label === 'string' && x.label.trim()).slice(0, 8)
    return o.options.length ? o : null
  } catch { return null }
}

export function OptionCard({ block, onChoose, disabled }: { block: OptionBlock; onChoose?: (text: string) => void; disabled?: boolean }) {
  const [picked, setPicked] = useState<string[]>([])
  const multi = block.mode === 'multi'
  return (
    <div className="my-2 rounded-xl border border-accent/40 bg-accent/5 p-3" data-options>
      {block.question && <p className="mb-2 text-sm font-medium">{block.question}</p>}
      <div className="flex flex-col gap-1.5">
        {block.options.map(o => {
          const on = picked.includes(o.label)
          return (
            <button key={o.label} type="button" disabled={disabled || !onChoose} onClick={() => multi ? setPicked(p => on ? p.filter(x => x !== o.label) : [...p, o.label]) : onChoose?.(o.label)}
              className={`flex w-full items-start gap-2 rounded-lg border px-3 py-1.5 text-left text-sm transition hover:bg-raised disabled:cursor-default disabled:opacity-70 ${on ? 'border-accent bg-accent/20' : 'border-line bg-glass'}`}>
              {multi && <span className={`mt-0.5 inline-block size-3.5 shrink-0 rounded border ${on ? 'border-accent bg-accent' : 'border-line'}`} />}
              <span className="min-w-0"><span className="font-medium">{o.label}</span>{o.detail && <span className="block text-xs text-muted">{o.detail}</span>}</span>
            </button>)
        })}
      </div>
      {multi && <div className="mt-2 flex justify-end"><button type="button" disabled={disabled || !onChoose || picked.length === 0} onClick={() => onChoose?.(picked.join(', '))} className="rounded-full bg-accent px-3 py-1 font-mono text-[11px] uppercase tracking-wider text-white disabled:opacity-50">Send choice{picked.length > 1 ? 's' : ''}</button></div>}
    </div>
  )
}

/** GFM markdown for assistant bubbles: code blocks get a Copy button, tables scroll inside their own box,
 *  ```hq-options blocks become option cards (only once the fence has closed — mid-stream they stay as code). */
export function Markdown({ text, onChoose, optionsDisabled }: { text: string; onChoose?: (t: string) => void; optionsDisabled?: boolean }) {
  return (
    <div className="md min-w-0 break-words text-sm leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        pre: ({ children }) => {
          const child = Array.isArray(children) ? children[0] : children
          const cls = (child && typeof child === 'object' && 'props' in child ? (child as { props: { className?: string } }).props.className : '') ?? ''
          if (/language-hq-options/.test(cls)) { const block = parseOptions(textOf(children)); if (block) return <OptionCard block={block} onChoose={onChoose} disabled={optionsDisabled} /> }
          return <div className="group/code relative my-2"><pre className="overflow-x-auto rounded-lg border border-line bg-inset p-3 font-mono text-[12px] leading-snug">{children}</pre><CopyBtn text={textOf(children)} /></div>
        },
        code: ({ className, children, ...p }) => className || String(children).includes('\n')
          ? <code className={className} {...p}>{children}</code>
          : <code className="rounded bg-inset px-1 py-0.5 font-mono text-[12px]" {...p}>{children}</code>,
        table: ({ children }) => <div className="my-2 overflow-x-auto"><table className="min-w-max border-collapse text-xs">{children}</table></div>,
        th: ({ children }) => <th className="border border-line bg-inset px-2 py-1 text-left font-semibold">{children}</th>,
        td: ({ children }) => <td className="border border-line px-2 py-1 align-top">{children}</td>,
        a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" className="text-accent-2 underline decoration-accent-2/40 hover:decoration-accent-2">{children}</a>,
        ul: ({ children }) => <ul className="my-1.5 list-disc pl-5">{children}</ul>,
        ol: ({ children }) => <ol className="my-1.5 list-decimal pl-5">{children}</ol>,
        li: ({ children }) => <li className="my-0.5">{children}</li>,
        h1: ({ children }) => <h1 className="mb-1 mt-3 text-base font-semibold">{children}</h1>,
        h2: ({ children }) => <h2 className="mb-1 mt-3 text-sm font-semibold">{children}</h2>,
        h3: ({ children }) => <h3 className="mb-1 mt-2 text-sm font-semibold">{children}</h3>,
        p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
        blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-accent/50 pl-3 text-muted">{children}</blockquote>,
        hr: () => <hr className="my-3 border-line" />,
      }}>{text}</ReactMarkdown>
    </div>
  )
}
