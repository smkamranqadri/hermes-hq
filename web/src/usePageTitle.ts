import { useEffect } from 'react'
// Per-page document title: "Tasks · Hermes HQ".
export function usePageTitle(title?: string) {
  useEffect(() => { document.title = title ? `${title} · Hermes HQ` : 'Hermes HQ' }, [title])
}
