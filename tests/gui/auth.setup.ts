import { test as setup, expect } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'

const authFile = '.auth/admin.json'

setup('authenticate as admin', async ({ page }) => {
  const user = process.env.E2E_USER ?? 'admin'
  const pass = process.env.E2E_PASS ?? 'admin'

  // Navigate to root — Vue Router auth guard redirects unauthenticated users to /login
  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // Wait for the login form (survives any client-side redirects)
  await page.waitForSelector('[data-testid="input-username"]', { timeout: 15_000 })

  await page.fill('[data-testid="input-username"]', user)
  await page.fill('[data-testid="input-password"]', pass)
  await page.click('[data-testid="btn-login"]')

  // Wait for the sidebar nav to appear — it is only rendered after successful login.
  // Using a DOM element avoids relying on waitForURL / "load" events, which do not
  // fire for Vue Router's client-side (pushState) navigation.
  await page.waitForSelector('[data-testid="nav-home"]', { timeout: 15_000 })

  // Persist storage state (localStorage with JWT token)
  fs.mkdirSync(path.dirname(authFile), { recursive: true })
  await page.context().storageState({ path: authFile })
})
