/**
 * Playwright E2E-Tests für Demo-Modus (Issue #130)
 *
 * Testet:
 *  1. Demo-User kann das Dashboard sehen
 *  2. Demo-User kann Adapter-Ansicht sehen (schreibgeschützt)
 *  3. Demo-User kann alle weiteren Bereiche normal aufrufen
 */

import { test, expect } from '@playwright/test'

test('Demo-User sieht Dashboard (Übersicht)', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await expect(page.getByRole('heading', { name: 'Übersicht', level: 2 })).toBeVisible({ timeout: 8_000 })
})

test('Demo-User sieht Adapter-Ansicht mit Demo-Modus Banner', async ({ page }) => {
  await page.goto('/adapters')
  await page.waitForLoadState('networkidle')
  await expect(page.getByText('Demo-Modus')).toBeVisible({ timeout: 8_000 })
})

test('Demo-User sieht keinen "Neue Instanz" Button in Adapter-Ansicht', async ({ page }) => {
  await page.goto('/adapters')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-testid="btn-new-instance"]')).not.toBeVisible()
})

test('Demo-User kann Objekte aufrufen', async ({ page }) => {
  await page.goto('/datapoints')
  await page.waitForLoadState('networkidle')
  await expect(page).toHaveURL('/datapoints')
})

test('Demo-User kann Historie aufrufen', async ({ page }) => {
  await page.goto('/history')
  await page.waitForLoadState('networkidle')
  await expect(page).toHaveURL('/history')
})

test('Demo-User kann Monitor aufrufen', async ({ page }) => {
  await page.goto('/ringbuffer')
  await page.waitForLoadState('networkidle')
  await expect(page).toHaveURL('/ringbuffer')
})

test('Demo-User kann Logikmodul aufrufen', async ({ page }) => {
  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  await expect(page).toHaveURL('/logic')
})

test('Sidebar zeigt Demo-User alle Navigationspunkte', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-testid="nav-home"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-adapters"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-datapoints"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-history"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-ringbuffer"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-logic"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-settings"]')).toBeVisible()
})
