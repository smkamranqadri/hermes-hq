import { useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return <button type="button" onClick={() => { void navigator.clipboard?.writeText(text).then(() => { setOk(true); setTimeout(() => setOk(false), 1200) }) }}
    className="absolute right-1.5 top-1.5 rounded-md border border-line bg-glass px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted opacity-0 transition hover:text-fg group-hover/code:opacity-100 focus:opacity-100">{ok ? 'copied' : 'copy'}</button>
}

function textOf(n: ReactNode): string {
  if (typeof n === 'string' || typeof n === 'number') return String(n)
  if (Array.isArray(n)) return n.map(textOf).join('')
  if (n && typeof n === 'object' && 'props' in n) return textOf((n as { props: { children?: ReactNode } }).props.children)
  return ''
}

/** GFM markdown for assistant bubbles: code blocks get a Copy button, tables scroll inside their own box. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md min-w-0 break-words text-sm leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        pre: ({ children }) => <div className="group/code relative my-2"><pre className="overflow-x-auto rounded-lg border border-line bg-inset p-3 font-mono text-[12px] leading-snug">{children}</pre><CopyBtn text={textOf(children)} /></div>,
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
