import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// Vitest configuration for Vue 3 component characterization tests.
// Pinia + Vue Test Utils + happy-dom emulate enough of a browser to mount
// RingBufferView.vue with mocked API and websocket dependencies.
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    include: ['tests/**/*.spec.js'],
    setupFiles: ['tests/setup.js'],
    testTimeout: 20000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary', 'html', 'lcov', 'json-summary'],
      include: ['src/**/*.{js,vue}'],
      exclude: ['src/main.js', 'src/router/**'],
    },
  },
})
