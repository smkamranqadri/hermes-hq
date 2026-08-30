import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: '../backend/static', emptyOutDir: true },
  server: { proxy: { '/api': { target: 'http://127.0.0.1:9010', ws: true } } },
})
