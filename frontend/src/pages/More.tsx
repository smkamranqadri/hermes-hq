import { useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { TOOLS } from '../components/TopBar'
import { GlassCard } from '../components/GlassCard'
import { Label } from '../components/ui'
import { Btn } from '../components/Modal'
import { usePageTitle } from '../usePageTitle'
import { post } from '../api'
import { THEMES, FONTS, applyTheme, applyFonts, readThemePref, readFontPref, type ThemePref, type FontId } from '../theme'

/** Phone "More" tab: everything the desktop top bar keeps behind Tools / the theme picker / SYS. */
export function More() {
  usePageTitle('More')
  const [theme, setTheme] = useState<ThemePref>(() => readThemePref())
  const [font, setFont] = useState<FontId>(() => readFontPref())
  const row = 'flex items-center justify-between border-b border-line-subtle px-4 py-3 text-sm last:border-0 hover:bg-raised'
  return (
    <section className="mx-auto max-w-6xl p-4 sm:p-6">
      <h1 className="mb-4 text-xl font-semibold tracking-tight">More</h1>
      <Label>Tools</Label>
      <GlassCard className="mb-5 mt-2 p-0">
        {[['Agents', '/agents'] as const, ...TOOLS, ['System', '/system'] as const].map(([label, to]) => <Link key={to} to={to} className={row}><span>{label}</span><span className="text-muted">›</span></Link>)}
      </GlassCard>
      <Label>Appearance</Label>
      <GlassCard className="mb-5 mt-2 p-3">
        <p className="mb-1.5 font-mono text-[10px] uppercase tracking-widest text-muted">Theme</p>
        <div className="grid grid-cols-2 gap-2">
          {THEMES.map(t => <button key={t.id} type="button" onClick={() => { setTheme(t.id); applyTheme(t.id) }} className={clsx('flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-xs', theme === t.id ? 'border-accent bg-accent/10' : 'border-line')}>
            <span className="flex shrink-0 gap-0.5 rounded border border-line p-0.5">{t.swatch.map(c => <span key={c} className="size-3 rounded-sm" style={{ background: c }} />)}</span>
            <span className="font-mono uppercase tracking-wider">{t.label}</span></button>)}
        </div>
        <p className="mb-1.5 mt-3 font-mono text-[10px] uppercase tracking-widest text-muted">Font</p>
        <div className="grid grid-cols-2 gap-2">
          {FONTS.map(f => <button key={f.id} type="button" onClick={() => { setFont(f.id); applyFonts(f.id) }} className={clsx('rounded-lg border px-2.5 py-2 text-left text-sm', font === f.id ? 'border-accent bg-accent/10' : 'border-line')} style={{ fontFamily: f.body }}>{f.label}</button>)}
        </div>
      </GlassCard>
      <Btn kind="ghost" onClick={async () => { await post('/api/logout'); window.dispatchEvent(new Event('hq:unauthenticated')) }}>Sign out</Btn>
    </section>
  )
}
