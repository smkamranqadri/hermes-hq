import type { MessagePart, TurnOptions } from '../../api'

/** Attachments become OpenAI-style parts: images as data: URLs (downscaled), text files inlined as fenced blocks. */
export type Attachment = { id: string; kind: 'image' | 'text'; name: string; dataUrl?: string; text?: string; size: number }

const TEXT_EXT = /\.(md|txt|json|csv|ts|tsx|js|py|yaml|yml|toml|sh|html|css|sql|log)$/i
export const MAX_IMAGE_B64 = 1_400_000
export const MAX_TEXT_BYTES = 200_000

export async function fileToAttachment(f: File): Promise<Attachment> {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  if (f.type.startsWith('image/')) {
    const dataUrl = await downscale(f)
    if (dataUrl.length > MAX_IMAGE_B64) throw new Error(`${f.name}: still over 1 MB after downscaling`)
    return { id, kind: 'image', name: f.name || 'image', dataUrl, size: dataUrl.length }
  }
  if (f.type.startsWith('text/') || TEXT_EXT.test(f.name) || f.type === 'application/json') {
    if (f.size > MAX_TEXT_BYTES) throw new Error(`${f.name}: text files up to 200 KB`)
    return { id, kind: 'text', name: f.name, text: await f.text(), size: f.size }
  }
  throw new Error(`${f.name}: only images and text files can be attached`)
}

/** Resize to ≤1920px on the long edge and re-encode as JPEG, stepping quality down until it fits. PNG stays PNG when small. */
async function downscale(f: File): Promise<string> {
  const raw = await readAsDataUrl(f)
  if (raw.length <= MAX_IMAGE_B64 / 2 && (f.type === 'image/png' || f.type === 'image/jpeg' || f.type === 'image/webp')) return raw
  const img = await loadImage(raw)
  const scale = Math.min(1, 1920 / Math.max(img.width, img.height))
  const canvas = document.createElement('canvas'); canvas.width = Math.round(img.width * scale); canvas.height = Math.round(img.height * scale)
  canvas.getContext('2d')!.drawImage(img, 0, 0, canvas.width, canvas.height)
  for (const q of [0.85, 0.7, 0.55, 0.4]) { const out = canvas.toDataURL('image/jpeg', q); if (out.length <= MAX_IMAGE_B64) return out }
  return canvas.toDataURL('image/jpeg', 0.3)
}
const readAsDataUrl = (f: Blob) => new Promise<string>((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = () => rej(r.error); r.readAsDataURL(f) })
const loadImage = (src: string) => new Promise<HTMLImageElement>((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('bad image')); i.src = src })

export function buildMessage(text: string, atts: Attachment[]): string | MessagePart[] {
  if (atts.length === 0) return text
  const parts: MessagePart[] = []
  const texts = [text.trim(), ...atts.filter(a => a.kind === 'text').map(a => `\`\`\`${a.name}\n${a.text}\n\`\`\``)].filter(Boolean)
  if (texts.length) parts.push({ type: 'text', text: texts.join('\n\n') })
  for (const a of atts) if (a.kind === 'image' && a.dataUrl) parts.push({ type: 'image_url', image_url: { url: a.dataUrl } })
  return parts
}

/** Per-session turn options (model / reasoning effort / fast) — browser-local. */
const optsKey = (profile: string, id: string | undefined) => `hq-chat-opts:${profile}/${id ?? 'new'}`
export function loadOpts(profile: string, id?: string): TurnOptions { try { return JSON.parse(localStorage.getItem(optsKey(profile, id)) ?? '{}') } catch { return {} } }
export function saveOpts(profile: string, id: string | undefined, o: TurnOptions) { try { const clean = Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== '' && v !== null)); if (Object.keys(clean).length) localStorage.setItem(optsKey(profile, id), JSON.stringify(clean)); else localStorage.removeItem(optsKey(profile, id)) } catch {} }

/** Unsent text survives a reload for 5 minutes (draft while typing, pending while a send is in flight). */
const draftKey = (profile: string, id: string | undefined) => `hq-chat-draft:${profile}/${id ?? 'new'}`
export function saveDraft(profile: string, id: string | undefined, text: string, pending = false) { try { if (text.trim()) localStorage.setItem(draftKey(profile, id), JSON.stringify({ text, ts: Date.now(), pending })); else localStorage.removeItem(draftKey(profile, id)) } catch {} }
export function takeDraft(profile: string, id?: string): { text: string; pending: boolean } | null {
  try { const raw = localStorage.getItem(draftKey(profile, id)); if (!raw) return null; const d = JSON.parse(raw) as { text: string; ts: number; pending?: boolean }; if (Date.now() - d.ts > 5 * 60_000) { localStorage.removeItem(draftKey(profile, id)); return null } return { text: d.text, pending: !!d.pending } } catch { return null }
}
export function clearDraft(profile: string, id?: string) { try { localStorage.removeItem(draftKey(profile, id)) } catch {} }

/** hq-owned slash commands (the gateway has no command endpoint; these act locally or via hq routes). */
export type Slash = { cmd: string; args?: string; desc: string }
export const SLASH: Slash[] = [
  { cmd: '/model', args: '<id>', desc: 'Use another model for this session (blank = gateway default)' },
  { cmd: '/reasoning', args: 'none|low|medium|high|xhigh|max', desc: 'Reasoning effort for this session' },
  { cmd: '/fast', args: 'on|off', desc: 'Priority service tier (thinking off)' },
  { cmd: '/title', args: '<text>', desc: 'Rename this session' },
  { cmd: '/pin', desc: 'Pin this session' },
  { cmd: '/unpin', desc: 'Unpin this session' },
  { cmd: '/export', desc: 'Download the transcript as Markdown' },
  { cmd: '/find', args: '<text>', desc: 'Find in this conversation' },
  { cmd: '/new', desc: 'Start a new session with this agent' },
  { cmd: '/steer', args: '<text>', desc: 'Guide the running turn without stopping it' },
  { cmd: '/help', desc: 'List these commands' },
]
export function matchSlash(text: string): Slash[] | null {
  if (!text.startsWith('/') || text.includes('\n')) return null
  const head = text.split(' ')[0].toLowerCase()
  if (text.includes(' ')) return null
  return SLASH.filter(s => s.cmd.startsWith(head))
}
