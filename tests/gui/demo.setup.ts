import { test as setup } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'

const authFile = '.auth/demo.json'

setup('authenticate as demo', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('[data-testid="input-username"]', { timeout: 15_000 })
  await page.fill('[data-testid="input-username"]', 'demo')
  await page.fill('[data-testid="input-password"]', 'demo')
  await page.click('[data-testid="btn-login"]')
  await page.waitForSelector('[data-testid="nav-home"]', { timeout: 15_000 })
  fs.mkdirSync(path.dirname(authFile), { recursive: true })
  await page.context().storageState({ path: authFile })
})
