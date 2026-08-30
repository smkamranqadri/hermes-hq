import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { updateSession, deleteSession, useChatSearch, ago, ApiError, type AgentSession } from '../../api'
import { Modal, TextInput, Btn } from '../Modal'
import { useToast } from '../Toast'
import { Loading } from '../ui'

const errText = (e: unknown) => e instanceof ApiError ? e.message : String(e)

/** ⋯ menu per session: pin, rename, export, delete. Rename/delete/pin go through the agent's gateway. */
export function SessionMenu({ profile, s, current, onRename }: { profile: string; s: AgentSession; current: boolean; onRename: () => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient(); const toast = useToast(); const nav = useNavigate()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { if (!open) return; const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }; document.addEventListener('mousedown', h); return () => document.removeEventListener('mousedown', h) }, [open])
  const refresh = () => { qc.invalidateQueries({ queryKey: ['agent-sessions', profile] }); qc.invalidateQueries({ queryKey: ['session', profile, s.id] }); qc.invalidateQueries({ queryKey: ['chat-scoped'] }) }
  async function run(fn: () => Promise<unknown>, done?: string) {
    setBusy(true)
    try { await fn(); refresh(); if (done) toast(done) } catch (e) { toast(errText(e), 'err') } finally { setBusy(false); setOpen(false) }
  }
  const pinned = !!s.pinned
  return (
    <div ref={ref} className="relative shrink-0">
      <button type="button" aria-label="Session menu" onClick={e => { e.preventDefault(); e.stopPropagation(); setOpen(o => !o) }} className={clsx('rounded px-1 font-mono text-[12px] leading-none text-muted hover:bg-inset hover:text-fg', open ? 'opacity-100' : 'opacity-0 group-hover/sess:opacity-100 focus:opacity-100')}>⋯</button>
      {open && (
        <div className="absolute right-0 top-5 z-20 w-40 rounded-lg border border-line hq-menu p-1 text-xs shadow-lg" onClick={e => e.stopPropagation()}>
          <button type="button" disabled={busy} onClick={() => void run(() => updateSession(profile, s.id, { pinned: !pinned }))} className="block w-full rounded px-2 py-1 text-left hover:bg-raised">{pinned ? 'Unpin' : 'Pin'}</button>
          <button type="button" disabled={busy} onClick={() => { setOpen(false); onRename() }} className="block w-full rounded px-2 py-1 text-left hover:bg-raised">Rename</button>
          <a href={`/api/session/${profile}/${s.id}/export.md`} download className="block w-full rounded px-2 py-1 text-left hover:bg-raised" onClick={() => setOpen(false)}>Export Markdown</a>
          <button type="button" disabled={busy} onClick={() => { if (window.confirm(`Delete "${s.title || s.id}"? The transcript is removed from ${profile}'s Hermes history.`)) void run(async () => { await deleteSession(profile, s.id); if (current) nav(`/chat/${profile}`) }, 'Session deleted') }} className="block w-full rounded px-2 py-1 text-left text-needsyou hover:bg-raised">Delete</button>
        </div>
      )}
    </div>
  )
}

export function RenameDialog({ profile, id, initial, onClose }: { profile: string; id: string; initial: string; onClose: () => void }) {
  const [title, setTitle] = useState(initial)
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient(); const toast = useToast()
  async function save() {
    if (!title.trim() || busy) return
    setBusy(true)
    try { await updateSession(profile, id, { title: title.trim() }); qc.invalidateQueries({ queryKey: ['agent-sessions', profile] }); qc.invalidateQueries({ queryKey: ['session', profile, id] }); qc.invalidateQueries({ queryKey: ['chat-scoped'] }); onClose() }
    catch (e) { toast(errText(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Modal title="Rename session" onClose={onClose}>
      <TextInput value={title} autoFocus maxLength={120} onChange={e => setTitle(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void save() }} />
      <div className="mt-3 flex justify-end gap-2"><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn busy={busy} onClick={() => void save()} disabled={!title.trim()}>Save</Btn></div>
    </Modal>
  )
}

/** Ctrl+K: search titles + message content across every agent's history (hq-side, works with gateways off). */
export function SearchModal({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const res = useChatSearch(q)
  const nav = useNavigate()
  const hits = res.data?.results ?? []
  useEffect(() => { setSel(0) }, [q])
  const go = (i: number) => { const h = hits[i]; if (h) { nav(`/chat/${h.profile}/${h.id}`, { state: { find: q } }); onClose() } }
  return (
    <Modal title="Search chats" onClose={onClose}>
      <TextInput value={q} autoFocus placeholder="Search titles and messages across all agents…" onChange={e => setQ(e.target.value)}
        onKeyDown={e => { if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(hits.length - 1, s + 1)) } else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => Math.max(0, s - 1)) } else if (e.key === 'Enter') go(sel) }} />
      <div className="mt-3 max-h-[50vh] overflow-y-auto">
        {q.trim().length < 2 && <p className="px-1 text-xs text-muted">Type at least two characters.</p>}
        {res.isFetching && <Loading rows={2} />}
        {!res.isFetching && q.trim().length >= 2 && hits.length === 0 && <p className="px-1 text-xs text-muted">No matches.</p>}
        {hits.map((h, i) => (
          <button key={`${h.profile}/${h.id}`} type="button" onMouseEnter={() => setSel(i)} onClick={() => go(i)} className={clsx('block w-full rounded-lg px-2 py-1.5 text-left text-xs hover:bg-raised', i === sel && 'bg-raised')}>
            <div className="flex items-center gap-2"><span className="font-mono text-[10px] text-accent-2">{h.profile}</span><span className="min-w-0 flex-1 truncate">{h.title || h.id}</span><span className="font-mono text-[10px] text-muted">{h.hits ? `${h.hits} hit${h.hits > 1 ? 's' : ''} · ` : ''}{ago(h.last_activity_at)}</span></div>
            {h.snippet && <p className="mt-0.5 truncate text-muted">{h.snippet}</p>}
          </button>))}
      </div>
    </Modal>
  )
}

/** Ctrl+F inside a transcript: wraps matches in <mark>, counts them, Enter / Shift+Enter walk them. */
export function FindBar({ container, initial, onClose }: { container: React.RefObject<HTMLDivElement | null>; initial?: string; onClose: () => void }) {
  const [q, setQ] = useState(initial ?? '')
  const [n, setN] = useState(0)
  const [i, setI] = useState(0)
  useEffect(() => {
    const root = container.current; if (!root) return
    // unwrap previous marks
    root.querySelectorAll('mark[data-find]').forEach(m => { const t = document.createTextNode(m.textContent ?? ''); m.replaceWith(t) }); root.normalize()
    const needle = q.trim().toLowerCase(); if (!needle) { setN(0); return }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); const nodes: Text[] = []
    for (let t = walker.nextNode(); t; t = walker.nextNode()) if (t.textContent && t.textContent.toLowerCase().includes(needle)) nodes.push(t as Text)
    let count = 0
    for (const t of nodes) {
      const text = t.textContent ?? ''; const frag = document.createDocumentFragment(); let pos = 0; const lower = text.toLowerCase()
      for (let at = lower.indexOf(needle); at >= 0; at = lower.indexOf(needle, at + needle.length)) {
        frag.append(text.slice(pos, at)); const m = document.createElement('mark'); m.dataset.find = String(count++); m.className = 'rounded bg-queued/40 text-fg'; m.textContent = text.slice(at, at + needle.length); frag.append(m); pos = at + needle.length
      }
      frag.append(text.slice(pos)); t.replaceWith(frag)
    }
    setN(count); setI(0)
    return () => { root.querySelectorAll('mark[data-find]').forEach(m => { const t = document.createTextNode(m.textContent ?? ''); m.replaceWith(t) }); root.normalize() }
  }, [q, container])
  useEffect(() => {
    const root = container.current; if (!root || !n) return
    root.querySelectorAll('mark[data-find]').forEach(m => m.classList.remove('ring-2', 'ring-accent'))
    const cur = root.querySelector(`mark[data-find="${i}"]`); if (cur) { cur.classList.add('ring-2', 'ring-accent'); cur.scrollIntoView({ block: 'center' }) }
  }, [i, n, container])
  return (
    <div className="mb-2 flex items-center gap-2 rounded-lg border border-line bg-inset px-2 py-1 text-xs">
      <input autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder="Find in conversation" className="min-w-0 flex-1 bg-transparent outline-none"
        onKeyDown={e => { if (e.key === 'Escape') onClose(); if (e.key === 'Enter') { e.preventDefault(); if (n) setI(x => e.shiftKey ? (x - 1 + n) % n : (x + 1) % n) } }} />
      <span className="font-mono text-[10px] text-muted">{q.trim() ? (n ? `${i + 1}/${n}` : '0/0') : ''}</span>
      <button type="button" onClick={() => n && setI(x => (x - 1 + n) % n)} className="px-1 text-muted hover:text-fg" aria-label="Previous">↑</button>
      <button type="button" onClick={() => n && setI(x => (x + 1) % n)} className="px-1 text-muted hover:text-fg" aria-label="Next">↓</button>
      <button type="button" onClick={onClose} className="px-1 text-muted hover:text-fg" aria-label="Close find">✕</button>
    </div>
  )
}
