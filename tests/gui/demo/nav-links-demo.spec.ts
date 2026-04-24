/**
 * Playwright E2E-Tests — Linkverwaltung Demo-Modus (Issue #223)
 *
 * Testet:
 *  1. Demo-User sieht den Links-Tab NICHT
 *  2. Demo-User sieht bestehende Links in der Sidebar (trotz Demo-Modus)
 *
 * Hinweis: Dieser Test-File läuft im "demo"-Playwright-Projekt
 * (storageState: .auth/demo.json) — kein Admin-Zugriff.
 */

import { test, expect } from '@playwright/test'
import { apiPost, apiDelete } from '../helpers'

// ---------------------------------------------------------------------------
// Test 1: Demo-User sieht keinen Links-Tab
// ---------------------------------------------------------------------------

test('Demo-User sieht keinen Links-Tab in den Einstellungen', async ({ page }) => {
  await page.goto('/settings')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('button:has-text("Links")')).not.toBeVisible({ timeout: 5_000 })
})

// ---------------------------------------------------------------------------
// Test 2: Demo-User sieht bestehende Custom Links in der Sidebar
// ---------------------------------------------------------------------------

test('Demo-User sieht angelegte Links in der Sidebar', async ({ page }) => {
  // Link per Admin-API anlegen (helpers lesen Admin-Token)
  const data = await apiPost('/api/v1/system/nav-links', {
    label: 'Demo-Sichtbar',
    url: 'https://demo-visible.example',
    icon: '',
    sort_order: 99,
    open_new_tab: true,
  }) as { id: string }
  const id = data.id

  try {
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await expect(page.locator(`[data-testid="nav-custom-link-${id}"]`)).toBeVisible({ timeout: 5_000 })
    await expect(page.locator(`[data-testid="nav-custom-link-${id}"]`)).toContainText('Demo-Sichtbar')
  } finally {
    await apiDelete(`/api/v1/system/nav-links/${id}`)
  }
})
