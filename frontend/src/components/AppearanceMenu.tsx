import { useState } from 'react'
import clsx from 'clsx'
import { Menu } from './Menu'
import { THEMES, FONTS, applyTheme, applyFonts, readThemePref, readFontPref, type ThemePref, type FontId } from '../theme'

// Theme swatches with descriptions + font groups, modelled on the Hermes dashboard picker.
export function AppearanceMenu() {
  const [theme, setTheme] = useState<ThemePref>(() => readThemePref())
  const [font, setFont] = useState<FontId>(() => readFontPref())
  const pickTheme = (t: ThemePref) => { setTheme(t); applyTheme(t) }
  const pickFont = (id: FontId) => { setFont(id); applyFonts(id) }
  const row = (active: boolean, onClick: () => void, children: React.ReactNode, cls?: string) => (
    <button type="button" onClick={onClick}
      className={clsx('flex w-full items-center gap-3 rounded-md px-2.5 py-1.5 text-left hover:bg-raised', active && 'bg-raised', cls)}>
      {children}
      {active && <span className="ml-auto text-accent-2">✓</span>}
    </button>
  )
  return (
    <Menu keepOpen button={<span className="font-mono text-[10px]">◐<span className="hidden sm:inline"> {THEMES.find(t => t.id === theme)?.label.toUpperCase() ?? 'VIOLET'}</span></span>}>
      <div className="max-h-[70vh] w-[min(18rem,calc(100vw-2rem))] overflow-y-auto p-1">
        <p className="px-2.5 pb-1 pt-1 font-mono text-[10px] uppercase tracking-widest text-muted">Theme</p>
        {THEMES.map(t => row(theme === t.id, () => pickTheme(t.id), (<>
          <span className="flex shrink-0 gap-0.5 rounded border border-line p-0.5">
            {t.swatch.map(c => <span key={c} className="size-3 rounded-sm" style={{ background: c }} />)}
          </span>
          <span><span className="block font-mono text-xs uppercase tracking-wider">{t.label}</span>
            <span className="block text-[11px] text-muted">{t.desc}</span></span>
        </>)))}
        <div className="my-2 border-t border-line" />
        <p className="px-2.5 pb-1 font-mono text-[10px] uppercase tracking-widest text-muted">Font</p>
        {FONTS.map(f => row(font === f.id, () => pickFont(f.id), (
          <span><span className="block text-sm" style={{ fontFamily: f.body }}>{f.label}</span>
            <span className="block text-[11px] text-muted">{f.desc}</span></span>
        )))}
      </div>
    </Menu>
  )
}
