import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  base: '/visu/',

  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },

  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true,
      },
    },
  },

  build: {
    outDir: '../frontend_dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  },
})
