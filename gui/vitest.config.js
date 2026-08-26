import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// Vitest configuration for Vue 3 component characterization tests.
// Pinia + Vue Test Utils + happy-dom emulate enough of a browser to mount
// RingBufferView.vue with mocked API and websocket dependencies.
export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: '"test"',
  },
  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },
  test: {
    environment: 'happy-dom',
    // The HelpDrawer (#896) renders a real <iframe src="/help/...">; without
    // this, happy-dom attempts an actual network fetch for the iframe content
    // on every test that mounts it, logging ECONNREFUSED noise (harmless —
    // assertions only check the src attribute — but pollutes test output).
    environmentOptions: {
      happyDOM: { settings: { disableIframePageLoading: true } },
    },
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
