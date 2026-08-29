// Theme + font preferences. Themes swap --hq-* vars via [data-theme];
// fonts set --hq-font-body / --hq-font-mono inline on <html>.
export const THEMES = [
  { id: 'violet', label: 'Violet', desc: 'Mission Control — violet and cyan on deep ink', swatch: ['#15151F', '#8B5CF6', '#7DD3FC'] },
  { id: 'violet-light', label: 'Violet Light', desc: 'Same palette on cream-white', swatch: ['#F6F4FB', '#7C3AED', '#38BDF8'] },
  { id: 'nous', label: 'Nous', desc: 'Dark teal, cream text, amber accent', swatch: ['#041c1c', '#ffe6cb', '#ffac02'] },
  { id: 'bronze', label: 'Bronze', desc: 'Charcoal with bronze accents', swatch: ['#0d0f12', '#b98a44', '#4c88c7'] },
  { id: 'slate', label: 'Slate', desc: 'Cool grey-blue, GitHub-like', swatch: ['#0d1117', '#7eb8f6', '#63d0a6'] },
  { id: 'hermes', label: 'Hermes', desc: 'Indigo-navy with indigo accent', swatch: ['#0a0e1a', '#6366f1', '#818cf8'] },
] as const
export type ThemeId = (typeof THEMES)[number]['id']
export type ThemePref = ThemeId | 'system'

export const FONT_GROUPS: { group: string; fonts: { id: string; label: string; stack: string }[] }[] = [
  { group: 'Sans', fonts: [
    { id: 'system-sans', label: 'System Sans', stack: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif' },
    { id: 'inter', label: 'Inter', stack: '"Inter", ui-sans-serif, system-ui, sans-serif' },
    { id: 'ibm-plex-sans', label: 'IBM Plex Sans', stack: '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif' },
    { id: 'work-sans', label: 'Work Sans', stack: '"Work Sans", ui-sans-serif, system-ui, sans-serif' },
    { id: 'atkinson', label: 'Atkinson Hyperlegible', stack: '"Atkinson Hyperlegible", ui-sans-serif, system-ui, sans-serif' },
    { id: 'dm-sans', label: 'DM Sans', stack: '"DM Sans", ui-sans-serif, system-ui, sans-serif' },
  ]},
  { group: 'Serif', fonts: [
    { id: 'system-serif', label: 'System Serif', stack: 'ui-serif, Georgia, "Times New Roman", serif' },
    { id: 'spectral', label: 'Spectral', stack: '"Spectral", ui-serif, Georgia, serif' },
    { id: 'fraunces', label: 'Fraunces', stack: '"Fraunces", ui-serif, Georgia, serif' },
    { id: 'source-serif', label: 'Source Serif 4', stack: '"Source Serif 4", ui-serif, Georgia, serif' },
  ]},
]
export const MONO_FONTS = [
  { id: 'system-mono', label: 'System Mono', stack: 'ui-monospace, Menlo, Consolas, monospace' },
  { id: 'jetbrains-mono', label: 'JetBrains Mono', stack: '"JetBrains Mono", ui-monospace, Menlo, monospace' },
  { id: 'ibm-plex-mono', label: 'IBM Plex Mono', stack: '"IBM Plex Mono", ui-monospace, Menlo, monospace' },
  { id: 'space-mono', label: 'Space Mono', stack: '"Space Mono", ui-monospace, Menlo, monospace' },
]
export const DEFAULT_FONTS = { body: 'inter', mono: 'jetbrains-mono' }
export type FontPref = { body: string; mono: string }

const KEY = 'hq-theme', FKEY = 'hq-fonts'
const get = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const set = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch {} }

function resolveTheme(pref: ThemePref): ThemeId {
  if (pref !== 'system') return pref
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'violet-light' : 'violet'
}
export function readThemePref(): ThemePref {
  const q = new URLSearchParams(window.location.search).get('theme')
  if (q) { set(KEY, q); return q as ThemePref }
  return (get(KEY) as ThemePref) || 'violet'
}
export function applyTheme(pref: ThemePref) {
  document.documentElement.setAttribute('data-theme', resolveTheme(pref))
  set(KEY, pref)
}
export function readFontPref(): FontPref {
  const q = new URLSearchParams(window.location.search).get('font')
  const stored = (() => { try { return JSON.parse(get(FKEY) || '{}') } catch { return {} } })()
  return { ...DEFAULT_FONTS, ...stored, ...(q ? { body: q } : {}) }
}
export function applyFonts(pref: FontPref) {
  const body = FONT_GROUPS.flatMap(g => g.fonts).find(f => f.id === pref.body) ?? FONT_GROUPS[0].fonts[1]
  const mono = MONO_FONTS.find(f => f.id === pref.mono) ?? MONO_FONTS[1]
  const root = document.documentElement.style
  root.setProperty('--hq-font-body', body.stack)
  root.setProperty('--hq-font-mono', mono.stack)
  set(FKEY, JSON.stringify(pref))
}
export function initAppearance() {
  applyTheme(readThemePref())
  applyFonts(readFontPref())
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    if (readThemePref() === 'system') applyTheme('system')
  })
}
