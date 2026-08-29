import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'
import { initAppearance } from './theme'
import '@fontsource/ibm-plex-sans/400.css'; import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/work-sans/400.css'; import '@fontsource/work-sans/600.css'
import '@fontsource/dm-sans/400.css'; import '@fontsource/dm-sans/600.css'
import '@fontsource/atkinson-hyperlegible/400.css'; import '@fontsource/atkinson-hyperlegible/700.css'
import '@fontsource/spectral/400.css'; import '@fontsource/spectral/600.css'
import '@fontsource/fraunces/400.css'; import '@fontsource/fraunces/600.css'
import '@fontsource/source-serif-4/400.css'; import '@fontsource/source-serif-4/600.css'
import '@fontsource/ibm-plex-mono/400.css'; import '@fontsource/space-mono/400.css'

initAppearance()

const qc = new QueryClient()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
