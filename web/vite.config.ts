import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/session': 'http://localhost:4096',
      '/provider': 'http://localhost:4096',
      '/agent': 'http://localhost:4096',
      '/event': 'http://localhost:4096',
      '/permission': 'http://localhost:4096',
      '/health': 'http://localhost:4096',
      '/file': 'http://localhost:4096',
      '/config': 'http://localhost:4096',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
