/**
 * Playwright E2E-Tests — Linkverwaltung (Issue #223)
 */

import { test, expect } from '@playwright/test'
import { apiPost, apiDelete, apiGet } from '../helpers'

// ── API-Helpers ───────────────────────────────────────────────────────────

async function createLinkViaApi(label: string, url: string): Promise<string> {
  const data = await apiPost('/api/v1/system/nav-links', {
    label, url, icon: '', sort_order: 99, open_new_tab: true,
  }) as { id: string }
  return data.id
}

async function deleteLinkViaApi(id: string): Promise<void> {
  await apiDelete(`/api/v1/system/nav-links/${id}`)
}

async function findLinkIdByLabel(label: string): Promise<string | null> {
  const data = await apiGet('/api/v1/system/nav-links') as Array<{ id: string; label: string }>
  return data.find(l => l.label === label)?.id ?? null
}

// ── Navigation zum Links-Tab ──────────────────────────────────────────────

async function gotoLinksTab(page: any) {
  await page.goto('/settings')
  await page.waitForLoadState('networkidle')
  await page.click('button:has-text("Links")')
  await expect(page.locator('[data-testid="links-tab"]')).toBeVisible({ timeout: 5_000 })
}

// ---------------------------------------------------------------------------
// Test 1: Links-Tab ist für Admin sichtbar
// ---------------------------------------------------------------------------

test('Admin sieht den Links-Tab in den Einstellungen', async ({ page }) => {
  await page.goto('/settings')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('button:has-text("Links")')).toBeVisible({ timeout: 5_000 })
})

// ---------------------------------------------------------------------------
// Test 2: Leerer Zustand wird angezeigt
// ---------------------------------------------------------------------------

test('Links-Tab zeigt Hinzufügen-Button', async ({ page }) => {
  await gotoLinksTab(page)
  await expect(page.locator('[data-testid="btn-add-nav-link"]')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 3: Link anlegen über die UI
// ---------------------------------------------------------------------------

test('Admin kann einen neuen Link anlegen', async ({ page }) => {
  const label = `E2E-Link-${Date.now()}`

  await gotoLinksTab(page)
  const tab = page.locator('[data-testid="links-tab"]')

  await page.click('[data-testid="btn-add-nav-link"]')
  await expect(page.locator('[data-testid="nav-link-form"]')).toBeVisible()

  await page.fill('[data-testid="input-nav-link-label"]', label)
  await page.fill('[data-testid="input-nav-link-url"]', 'https://example-e2e.test')
  await page.click('[data-testid="btn-save-nav-link"]')

  await expect(page.locator('[data-testid="nav-link-form"]')).not.toBeVisible({ timeout: 5_000 })
  await expect(tab.getByText(label)).toBeVisible({ timeout: 5_000 })

  // Cleanup
  const id = await findLinkIdByLabel(label)
  if (id) await deleteLinkViaApi(id)
})

// ---------------------------------------------------------------------------
// Test 4: Angelegter Link erscheint in der Sidebar
// ---------------------------------------------------------------------------

test('Angelegter Link erscheint in der Sidebar', async ({ page }) => {
  const label = `Sidebar-Link-${Date.now()}`
  const id = await createLinkViaApi(label, 'https://sidebar-test.example')

  try {
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await expect(page.locator(`[data-testid="nav-custom-link-${id}"]`)).toBeVisible({ timeout: 5_000 })
    await expect(page.locator(`[data-testid="nav-custom-link-${id}"]`)).toContainText(label)
  } finally {
    await deleteLinkViaApi(id)
  }
})

// ---------------------------------------------------------------------------
// Test 5: Link bearbeiten
// ---------------------------------------------------------------------------

test('Admin kann einen Link bearbeiten', async ({ page }) => {
  const id = await createLinkViaApi(`Original-${Date.now()}`, 'https://original.example')

  try {
    await gotoLinksTab(page)
    const tab = page.locator('[data-testid="links-tab"]')

    await expect(page.locator(`[data-testid="nav-link-row-${id}"]`)).toBeVisible({ timeout: 5_000 })

    await page.click(`[data-testid="nav-link-row-${id}"] button[title="Bearbeiten"]`)
    await expect(page.locator('[data-testid="nav-link-form"]')).toBeVisible()

    await page.fill('[data-testid="input-nav-link-label"]', 'Bearbeitet')
    await page.click('[data-testid="btn-save-nav-link"]')

    await expect(page.locator('[data-testid="nav-link-form"]')).not.toBeVisible({ timeout: 5_000 })
    await expect(tab.getByText('Bearbeitet')).toBeVisible({ timeout: 5_000 })
  } finally {
    await deleteLinkViaApi(id)
  }
})

// ---------------------------------------------------------------------------
// Test 6: Link löschen
// ---------------------------------------------------------------------------

test('Admin kann einen Link löschen', async ({ page }) => {
  const id = await createLinkViaApi('Zu löschen', 'https://delete.example')

  try {
    await gotoLinksTab(page)
    const tab = page.locator('[data-testid="links-tab"]')

    await expect(page.locator(`[data-testid="nav-link-row-${id}"]`)).toBeVisible({ timeout: 5_000 })
    await page.click(`[data-testid="btn-delete-nav-link-${id}"]`)
    await expect(page.locator(`[data-testid="nav-link-row-${id}"]`)).not.toBeVisible({ timeout: 5_000 })
    await expect(tab.getByText('Zu löschen')).not.toBeVisible()
  } finally {
    await deleteLinkViaApi(id).catch(() => {})
  }
})

// ---------------------------------------------------------------------------
// Test 7: Gelöschter Link verschwindet aus der Sidebar
// ---------------------------------------------------------------------------

test('Gelöschter Link verschwindet aus der Sidebar', async ({ page }) => {
  const id = await createLinkViaApi('Temp-Sidebar', 'https://temp-sidebar.example')

  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await expect(page.locator(`[data-testid="nav-custom-link-${id}"]`)).toBeVisible({ timeout: 5_000 })

  await deleteLinkViaApi(id)

  await page.reload()
  await page.waitForLoadState('networkidle')
  await expect(page.locator(`[data-testid="nav-custom-link-${id}"]`)).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 8: Abbrechen beim Formular
// ---------------------------------------------------------------------------

test('Formular-Abbrechen schliesst das Formular ohne Speichern', async ({ page }) => {
  await gotoLinksTab(page)
  const tab = page.locator('[data-testid="links-tab"]')

  await page.click('[data-testid="btn-add-nav-link"]')
  await expect(page.locator('[data-testid="nav-link-form"]')).toBeVisible()

  await page.fill('[data-testid="input-nav-link-label"]', 'Nicht gespeichert')
  await page.click('[data-testid="btn-cancel-nav-link"]')

  await expect(page.locator('[data-testid="nav-link-form"]')).not.toBeVisible({ timeout: 3_000 })
  await expect(tab.getByText('Nicht gespeichert')).not.toBeVisible()
})
