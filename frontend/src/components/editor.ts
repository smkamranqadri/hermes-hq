// Shared CodeMirror pieces (theme from --hq-* vars, highlight style, lazy grammar) for Files and Memory.
import { useEffect, useState } from 'react'
import { EditorView } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting, type LanguageSupport } from '@codemirror/language'
import { languages } from '@codemirror/language-data'
import { tags as t } from '@lezer/highlight'

export const cmTheme = EditorView.theme({
  '&': { backgroundColor: 'transparent', color: 'var(--hq-text)', fontSize: '13px', height: '100%' },
  '.cm-content': { fontFamily: 'var(--hq-font-mono)', caretColor: 'var(--hq-accent-2)', padding: '8px 0' },
  '.cm-scroller': { fontFamily: 'var(--hq-font-mono)', lineHeight: '1.55' },
  '.cm-gutters': { backgroundColor: 'transparent', color: 'var(--hq-muted)', border: 'none', opacity: '0.7' },
  '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'color-mix(in srgb, var(--hq-accent) 10%, transparent)' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': { backgroundColor: 'color-mix(in srgb, var(--hq-accent) 30%, transparent) !important' },
  '.cm-cursor': { borderLeftColor: 'var(--hq-accent-2)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-matchingBracket': { backgroundColor: 'color-mix(in srgb, var(--hq-accent-2) 25%, transparent)' },
})
export const cmHighlight = syntaxHighlighting(HighlightStyle.define([
  { tag: [t.keyword, t.modifier, t.operatorKeyword], color: 'var(--hq-accent)' },
  { tag: [t.string, t.special(t.string)], color: 'var(--hq-working)' },
  { tag: [t.number, t.bool, t.null, t.atom], color: 'var(--hq-needsyou)' },
  { tag: [t.comment, t.meta], color: 'var(--hq-muted)', fontStyle: 'italic' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.variableName)], color: 'var(--hq-accent-2)' },
  { tag: [t.typeName, t.className, t.tagName], color: 'var(--hq-queued)' },
  { tag: [t.propertyName, t.attributeName], color: 'var(--hq-done)' },
  { tag: t.heading, fontWeight: '600', color: 'var(--hq-accent-2)' },
  { tag: [t.link, t.url], color: 'var(--hq-accent-2)', textDecoration: 'underline' },
  { tag: t.emphasis, fontStyle: 'italic' }, { tag: t.strong, fontWeight: '600' },
  { tag: t.invalid, color: 'var(--hq-error)' },
]))

/** Lazy grammar for the open file's name (language-data loads each grammar on demand). */
export function useLanguage(name: string) {
  const [lang, setLang] = useState<LanguageSupport | null>(null)
  useEffect(() => {
    let alive = true
    const d = languages.find(l => l.extensions.some(e => name.toLowerCase().endsWith('.' + e)) || l.filename?.test(name))
    if (!d) { setLang(null); return }
    d.load().then(l => { if (alive) setLang(l) })
    return () => { alive = false }
  }, [name])
  return lang
}

