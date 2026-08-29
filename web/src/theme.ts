// Theme + font preferences. Themes swap --hq-* vars via [data-theme];
// fonts set --hq-font-body / --hq-font-mono inline on <html>.
export const THEMES = [
  { id: 'violet', label: 'Violet', desc: 'Mission Control — violet and cyan on deep ink', swatch: ['#15151F', '#8B5CF6', '#7DD3FC'] },
  { id: 'nous', label: 'Nous', desc: 'Dark teal, cream text, amber accent', swatch: ['#041c1c', '#ffe6cb', '#ffac02'] },
  { id: 'nous-light', label: 'Nous Light', desc: 'Light — off-white with navy and blue', swatch: ['#f8faf8', '#16315f', '#2557b7'] },
  { id: 'bronze', label: 'Bronze', desc: 'Charcoal with bronze accents', swatch: ['#0d0f12', '#b98a44', '#4c88c7'] },
  { id: 'slate', label: 'Slate', desc: 'Cool grey-blue, GitHub-like', swatch: ['#0d1117', '#7eb8f6', '#63d0a6'] },
  { id: 'hermes', label: 'Hermes', desc: 'Indigo-navy with indigo accent', swatch: ['#0a0e1a', '#6366f1', '#818cf8'] },
] as const
export type ThemeId = (typeof THEMES)[number]['id']
export type ThemePref = ThemeId

// One font choice. Bundled: Inter + JetBrains Mono (identical on every device).
// "System" entries use whatever the viewing device has installed.
export const FONTS = [
  { id: 'jetbrains-mono', label: 'JetBrains Mono', desc: 'Default — mono everywhere', body: '"JetBrains Mono", ui-monospace, Menlo, monospace', mono: '"JetBrains Mono", ui-monospace, Menlo, monospace' },
  { id: 'inter', label: 'Inter', desc: 'Bundled sans-serif', body: '"Inter", ui-sans-serif, system-ui, sans-serif', mono: '"JetBrains Mono", ui-monospace, Menlo, monospace' },
  { id: 'system-sans', label: 'System Sans', desc: 'Device sans-serif', body: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', mono: '"JetBrains Mono", ui-monospace, Menlo, monospace' },
  { id: 'system-serif', label: 'System Serif', desc: 'Device serif', body: 'ui-serif, Georgia, "Times New Roman", serif', mono: '"JetBrains Mono", ui-monospace, Menlo, monospace' },
  { id: 'system-mono', label: 'System Mono', desc: 'Device monospace everywhere', body: 'ui-monospace, Menlo, Consolas, monospace', mono: 'ui-monospace, Menlo, Consolas, monospace' },
] as const
export type FontId = (typeof FONTS)[number]['id']

const KEY = 'hq-theme', FKEY = 'hq-fonts'
const get = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const set = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch {} }

export function readThemePref(): ThemePref {
  const q = new URLSearchParams(window.location.search).get('theme')
  if (q) { set(KEY, q); return q as ThemePref }
  return (get(KEY) as ThemePref) || 'violet'
}
export function applyTheme(pref: ThemePref) {
  document.documentElement.setAttribute('data-theme', THEMES.some(t => t.id === pref) ? pref : 'violet')
  set(KEY, pref)
}
export function readFontPref(): FontId {
  const q = new URLSearchParams(window.location.search).get('font')
  if (q) { set(FKEY, q); return q as FontId }
  return (get(FKEY) as FontId) || 'jetbrains-mono'
}
export function applyFonts(id: FontId) {
  const f = FONTS.find(x => x.id === id) ?? FONTS[0]
  const root = document.documentElement.style
  root.setProperty('--hq-font-body', f.body)
  root.setProperty('--hq-font-mono', f.mono)
  set(FKEY, f.id)
}
export function initAppearance() {
  applyTheme(readThemePref())
  applyFonts(readFontPref())
}
