import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: '../hq/static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:9010' } },
})
