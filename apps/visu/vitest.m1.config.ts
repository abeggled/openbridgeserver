import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

/**
 * Dedicated vitest config for the M1 acceptance smoke (`pnpm m1:smoke`).
 *
 * The default `vitest.config.ts` only includes `tests/**` and `src/**`, so the
 * standalone `scripts/m1-smoke.ts` scenario stays out of the normal `-r test`
 * run and is driven only through this gate. No Vue plugin / jsdom: the smoke is
 * a pure core-contract check (no component mount), so it runs in plain Node.
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@obs/visu-contract': fileURLToPath(
        new URL('../../packages/contract/src/index.ts', import.meta.url),
      ),
    },
  },
  test: {
    environment: 'node',
    globals: true,
    include: ['scripts/m1-smoke.ts'],
  },
});
