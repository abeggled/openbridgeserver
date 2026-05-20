import * as path from 'path'
import * as dotenv from 'dotenv'
import { defineConfig } from '@playwright/test'

// Load the repository-root .env so OBS_HTTP_HOST_PORT et al. resolve the
// same way they do for `docker compose up`. Override precedence:
//   1. BASE_URL  (explicit)
//   2. OBS_HTTP_HOST_PORT  (matches the host port that compose publishes)
//   3. 8080  (built-in default)
dotenv.config({ path: path.resolve(__dirname, '..', '..', '.env') })

const obsHttpHostPort = process.env.OBS_HTTP_HOST_PORT ?? '8080'
const baseURL = process.env.BASE_URL ?? `http://localhost:${obsHttpHostPort}`

export default defineConfig({
  testDir: '.',
  fullyParallel: false,
  retries: 1,
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'admin-setup',
      testMatch: '**/auth.setup.ts',
    },
    {
      name: 'demo-setup',
      testMatch: '**/demo.setup.ts',
    },
    {
      name: 'admin',
      testMatch: '**/admin/**/*.spec.ts',
      dependencies: ['admin-setup'],
      use: { storageState: '.auth/admin.json' },
    },
    {
      name: 'visu',
      testMatch: '**/visu/**/*.spec.ts',
      dependencies: ['admin-setup'],
      use: { storageState: '.auth/admin.json' },
    },
    {
      name: 'demo',
      testMatch: '**/demo/**/*.spec.ts',
      dependencies: ['demo-setup'],
      use: { storageState: '.auth/demo.json' },
    },
  ],
})
