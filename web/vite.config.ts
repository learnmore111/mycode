import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(() => {
  const target = process.env.MYCODE_API_TARGET || 'http://127.0.0.1:4096'
  const routes = [
    '/health',
    '/api',
    '/metrics',
    '/agent',
    '/log',
    '/config',
    '/event',
    '/file',
    '/git',
    '/mcp',
    '/orchestration',
    '/permission',
    '/project',
    '/provider',
    '/session',
    '/skill',
  ]

  return {
    plugins: [react()],
    server: {
      port: 3000,
      strictPort: true,
      proxy: Object.fromEntries(
        routes.map((route) => [route, { target, changeOrigin: true }]),
      ),
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
    },
  }
})
