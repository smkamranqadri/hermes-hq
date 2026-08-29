import { useState } from 'react'
import clsx from 'clsx'
import { Menu } from './Menu'
import { THEMES, FONT_GROUPS, MONO_FONTS, applyTheme, applyFonts, readThemePref, readFontPref, type ThemePref, type FontPref } from '../theme'

// Theme swatches with descriptions + font groups, modelled on the Hermes dashboard picker.
export function AppearanceMenu() {
  const [theme, setTheme] = useState<ThemePref>(() => readThemePref())
  const [fonts, setFonts] = useState<FontPref>(() => readFontPref())
  const pickTheme = (t: ThemePref) => { setTheme(t); applyTheme(t) }
  const pickFont = (patch: Partial<FontPref>) => { const next = { ...fonts, ...patch }; setFonts(next); applyFonts(next) }
  const row = (active: boolean, onClick: () => void, children: React.ReactNode, cls?: string) => (
    <button type="button" onClick={onClick}
      className={clsx('flex w-full items-center gap-3 rounded-md px-2.5 py-1.5 text-left hover:bg-raised', active && 'bg-raised', cls)}>
      {children}
      {active && <span className="ml-auto text-accent-2">✓</span>}
    </button>
  )
  return (
    <Menu keepOpen button={<span className="font-mono text-[10px]">◐ {THEMES.find(t => t.id === theme)?.label.toUpperCase() ?? 'AUTO'}</span>}>
      <div className="max-h-[70vh] w-72 overflow-y-auto p-1">
        <p className="px-2.5 pb-1 pt-1 font-mono text-[10px] uppercase tracking-widest text-muted">Theme</p>
        {THEMES.map(t => row(theme === t.id, () => pickTheme(t.id), (<>
          <span className="flex shrink-0 gap-0.5 rounded border border-line p-0.5">
            {t.swatch.map(c => <span key={c} className="size-3 rounded-sm" style={{ background: c }} />)}
          </span>
          <span><span className="block font-mono text-xs uppercase tracking-wider">{t.label}</span>
            <span className="block text-[11px] text-muted">{t.desc}</span></span>
        </>)))}
        {row(theme === 'system', () => pickTheme('system'), (<>
          <span className="flex shrink-0 gap-0.5 rounded border border-line p-0.5"><span className="size-3 rounded-sm bg-[#15151F]" /><span className="size-3 rounded-sm bg-[#F6F4FB]" /><span className="size-3 rounded-sm bg-[#8B5CF6]" /></span>
          <span><span className="block font-mono text-xs uppercase tracking-wider">Auto</span>
            <span className="block text-[11px] text-muted">Follow the OS: Violet Light by day, Violet in dark mode</span></span>
        </>))}
        <div className="my-2 border-t border-line" />
        <p className="px-2.5 pb-1 font-mono text-[10px] uppercase tracking-widest text-muted">Font</p>
        {FONT_GROUPS.map(g => (
          <div key={g.group}>
            <p className="px-2.5 pt-2 pb-0.5 font-mono text-[9px] uppercase tracking-widest text-muted/70">{g.group}</p>
            {g.fonts.map(f => row(fonts.body === f.id, () => pickFont({ body: f.id }), <span style={{ fontFamily: f.stack }} className="text-sm">{f.label}</span>))}
          </div>
        ))}
        <p className="px-2.5 pt-2 pb-0.5 font-mono text-[9px] uppercase tracking-widest text-muted/70">Mono</p>
        {MONO_FONTS.map(f => row(fonts.mono === f.id, () => pickFont({ mono: f.id }), <span style={{ fontFamily: f.stack }} className="text-sm">{f.label}</span>))}
      </div>
    </Menu>
  )
}
