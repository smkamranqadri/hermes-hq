export const THEMES = [
  ['violet', 'Violet'], ['violet-light', 'Violet Light'], ['nous', 'Nous'],
  ['bronze', 'Bronze'], ['slate', 'Slate'], ['hermes', 'Hermes'],
] as const
export type ThemeId = (typeof THEMES)[number][0]
export type ThemePref = ThemeId | 'system'
const KEY = 'hq-theme'

function resolve(pref: ThemePref): ThemeId {
  if (pref !== 'system') return pref
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'violet-light' : 'violet'
}

export function readThemePref(): ThemePref {
  const q = new URLSearchParams(window.location.search).get('theme')
  if (q) { try { localStorage.setItem(KEY, q) } catch {} return q as ThemePref }
  try { return (localStorage.getItem(KEY) as ThemePref) || 'violet' } catch { return 'violet' }
}

export function applyTheme(pref: ThemePref) {
  document.documentElement.setAttribute('data-theme', resolve(pref))
  try { localStorage.setItem(KEY, pref) } catch {}
}

export function initTheme() {
  applyTheme(readThemePref())
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    if (readThemePref() === 'system') applyTheme('system')
  })
}
