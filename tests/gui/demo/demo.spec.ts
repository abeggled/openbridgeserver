/**
 * Playwright E2E-Tests für Demo-Modus (Issue #130)
 *
 * Testet:
 *  1. Demo-User kann das Dashboard sehen
 *  2. Demo-User kann Adapter-Ansicht sehen (schreibgeschützt)
 *  3. Demo-User wird von gesperrten Routen zum Dashboard umgeleitet
 *  4. Sidebar zeigt Demo-User nur erlaubte Navigationspunkte
 */

import { test, expect } from '@playwright/test'

test('Demo-User sieht Dashboard (Übersicht)', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await expect(page.getByRole('heading', { name: 'Übersicht' })).toBeVisible({ timeout: 8_000 })
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

test('Demo-User wird von /datapoints zum Dashboard umgeleitet', async ({ page }) => {
  await page.goto('/datapoints')
  await page.waitForURL('/', { timeout: 8_000 })
  await expect(page).toHaveURL('/')
})

test('Demo-User wird von /history zum Dashboard umgeleitet', async ({ page }) => {
  await page.goto('/history')
  await page.waitForURL('/', { timeout: 8_000 })
  await expect(page).toHaveURL('/')
})

test('Demo-User wird von /logic zum Dashboard umgeleitet', async ({ page }) => {
  await page.goto('/logic')
  await page.waitForURL('/', { timeout: 8_000 })
  await expect(page).toHaveURL('/')
})

test('Sidebar zeigt Demo-User nur Übersicht, Adapter und Einstellungen', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-testid="nav-home"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-adapters"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-settings"]')).toBeVisible()
  await expect(page.locator('[data-testid="nav-datapoints"]')).not.toBeVisible()
  await expect(page.locator('[data-testid="nav-history"]')).not.toBeVisible()
  await expect(page.locator('[data-testid="nav-logic"]')).not.toBeVisible()
  await expect(page.locator('[data-testid="nav-ringbuffer"]')).not.toBeVisible()
})
