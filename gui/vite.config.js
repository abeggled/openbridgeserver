import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],

  resolve: {
    alias: { '@': resolve(__dirname, 'src') }
  },

  // Dev server: proxy all /api calls to the OpenTWS backend
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true,            // WebSocket proxy
      }
    }
  },

  build: {
    outDir: '../gui_dist',   // output next to gui/ directory, served by FastAPI
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  }
})
