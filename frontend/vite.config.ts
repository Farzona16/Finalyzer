import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

const BACKEND = 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@shared': fileURLToPath(new URL('../shared', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // One origin in production; in development the proxy makes it look the same.
      // The backend compares the Origin header with its own host (CSRF), so the proxy
      // presents the backend's own origin.
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (request) => request.setHeader('origin', BACKEND))
        },
      },
    },
  },
  build: {
    target: 'es2023',
    sourcemap: false,
    chunkSizeWarningLimit: 350,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
  },
})
