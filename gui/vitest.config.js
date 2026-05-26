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
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary', 'html'],
      include: [
        'src/views/RingBufferView.vue',
        'src/views/ringbuffer/FilterEditor.vue',
        'src/views/ringbuffer/MonitorConfigModal.vue',
        'src/views/ringbuffer/TopbarFilterChips.vue',
        'src/views/ringbuffer/TopbarStats.vue',
        'src/views/ringbuffer/ExportDialog.vue',
        'src/components/ui/Combobox.vue',
        'src/components/ui/DpCombobox.vue',
        'src/components/ui/TagCombobox.vue',
        'src/components/ui/AdapterCombobox.vue',
        'src/components/ui/HierarchyCombobox.vue',
        'src/components/ui/PathLabel.vue',
        'src/components/ui/TimeFilterPopover.vue',
        'src/components/ui/Modal.vue',
        'src/composables/useSetColors.js',
        'src/composables/useLiveQueue.js',
        'src/composables/useTimeFilterParser.js',
        'src/composables/useTimeFilterPayload.js',
      ],
    },
  },
})
