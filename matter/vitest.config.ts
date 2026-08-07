import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['test/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.ts'],
      // src/devices/** and module.ts import 'matterbridge' directly and can only be exercised
      // against a linked Matterbridge install (see README.md) — excluded from the coverage
      // target here, the same way the plugin as a whole can't be unit-tested without the SDK.
      exclude: ['src/devices/**', 'src/module.ts'],
      thresholds: {
        lines: 95,
        branches: 90,
        functions: 95,
      },
    },
  },
});
