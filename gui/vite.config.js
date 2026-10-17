import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { readFileSync } from 'fs'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url)))

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [vue()],

  resolve: {
    alias: { '@': resolve(import.meta.dirname, 'src') }
  },

  // Dev server: proxy /api, /hook and /help to backend, /visu to the Visu frontend dev server (port 5174)
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true,            // WebSocket proxy
      },
      '/visu': {
        target: 'http://localhost:5174',
        changeOrigin: true,
      },
      '/help': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      // WEBHOOK adapter trigger endpoint (#1256). The binding form builds the
      // call URL from window.location.origin, which in dev is this dev server
      // — without this the copied URL would hit Vite's SPA fallback and the
      // backend would never see the call. Covers the default path prefix; an
      // instance configured with a different one must be called on :8080
      // directly while developing.
      '/hook': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    }
  },

  build: {
    outDir: '../gui_dist',   // output next to gui/ directory, served by FastAPI
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  }
})
