/**
 * Playwright E2E-Tests — Visu Duplizierung (Issue #240)
 *
 * Prüft: Kopieren-Button/Modal, Export-Link, Import-Button in TreeManager.
 */

import { test, expect } from '@playwright/test'
import { apiPost, apiDelete } from '../helpers'

// ── API-Helpers ───────────────────────────────────────────────────────────

async function createVisuNodeViaApi(name: string): Promise<string> {
  const data = await apiPost('/api/v1/visu/nodes', {
    name,
    type: 'PAGE',
    order: 99,
  }) as { id: string }
  return data.id
}

async function deleteVisuNodeViaApi(id: string): Promise<void> {
  await apiDelete(`/api/v1/visu/nodes/${id}`)
}

// ── Navigation zur Visu-Verwaltung ────────────────────────────────────────

async function gotoManageAndSelectNode(page: any, nodeName: string) {
  await page.goto('/visu/manage')
  await page.waitForLoadState('networkidle')
  await page.click(`text="${nodeName}"`)
  await expect(page.locator('text="Weitere Aktionen"')).toBeVisible({ timeout: 5_000 })
}

// ---------------------------------------------------------------------------
// Test 1: Kopieren-Button im Eigenschaften-Panel sichtbar
// ---------------------------------------------------------------------------

test('Visu: Kopieren-Button erscheint im Eigenschaften-Panel', async ({ page }) => {
  const name = `E2E-Kop-${Date.now()}`
  const nid  = await createVisuNodeViaApi(name)
  try {
    await gotoManageAndSelectNode(page, name)
    await expect(page.locator('button:has-text("Kopieren")')).toBeVisible({ timeout: 5_000 })
  } finally {
    await deleteVisuNodeViaApi(nid)
  }
})

// ---------------------------------------------------------------------------
// Test 2: Kopieren öffnet das Kopieren-Modal
// ---------------------------------------------------------------------------

test('Visu: Kopieren-Button öffnet das Kopieren-Modal', async ({ page }) => {
  const name = `E2E-KopModal-${Date.now()}`
  const nid  = await createVisuNodeViaApi(name)
  try {
    await gotoManageAndSelectNode(page, name)
    await page.click('button:has-text("Kopieren")')
    // Modal öffnet sich
    await expect(page.locator('text="Kopieren:"')).toBeVisible({ timeout: 3_000 })
    await expect(page.locator('text="Name der Kopie"')).toBeVisible()
    // Standardname ist vorausgefüllt
    const input = page.locator('input[type="text"]').last()
    const value = await input.inputValue()
    expect(value).toContain('Kopie von')
  } finally {
    await deleteVisuNodeViaApi(nid)
  }
})

// ---------------------------------------------------------------------------
// Test 3: Exportieren-Link ist sichtbar und zeigt auf korrekten Endpoint
// ---------------------------------------------------------------------------

test('Visu: Exportieren-Link zeigt auf Export-Endpoint', async ({ page }) => {
  const name = `E2E-Exp-${Date.now()}`
  const nid  = await createVisuNodeViaApi(name)
  try {
    await gotoManageAndSelectNode(page, name)
    const exportLink = page.locator('a:has-text("Exportieren")')
    await expect(exportLink).toBeVisible({ timeout: 5_000 })
    const href = await exportLink.getAttribute('href')
    expect(href).toContain(`/api/v1/visu/nodes/${nid}/export`)
  } finally {
    await deleteVisuNodeViaApi(nid)
  }
})

// ---------------------------------------------------------------------------
// Test 4: Importieren-Button im Header sichtbar
// ---------------------------------------------------------------------------

test('Visu: Importieren-Button ist im Header sichtbar', async ({ page }) => {
  await page.goto('/visu/manage')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-testid="btn-import-visu"]')).toBeVisible({ timeout: 5_000 })
})

// ---------------------------------------------------------------------------
// Test 5: Importieren-Button im Eigenschaften-Panel sichtbar
// ---------------------------------------------------------------------------

test('Visu: Importieren-Button ist im Eigenschaften-Panel sichtbar', async ({ page }) => {
  const name = `E2E-Imp-${Date.now()}`
  const nid  = await createVisuNodeViaApi(name)
  try {
    await gotoManageAndSelectNode(page, name)
    await expect(page.locator('button:has-text("Importieren")')).toBeVisible({ timeout: 5_000 })
  } finally {
    await deleteVisuNodeViaApi(nid)
  }
})
