/**
 * Playwright E2E-Tests — Logic Graph Duplizierung (Issue #240)
 *
 * Prüft: Duplizieren-Button, Exportieren-Link, Importieren (via API).
 */

import { test, expect } from '@playwright/test'
import { apiPost, apiDelete, apiGet } from '../helpers'

// ── API-Helpers ───────────────────────────────────────────────────────────

async function createGraphViaApi(name: string): Promise<string> {
  const data = await apiPost('/api/v1/logic/graphs', {
    name,
    description: '',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'n1', type: 'and', position: { x: 0, y: 0 }, data: { label: 'AND', input_count: 2 } },
      ],
      edges: [],
    },
  }) as { id: string }
  return data.id
}

async function deleteGraphViaApi(id: string): Promise<void> {
  await apiDelete(`/api/v1/logic/graphs/${id}`)
}

async function findCopyGraphId(originalName: string): Promise<string | null> {
  const graphs = await apiGet('/api/v1/logic/graphs') as Array<{ id: string; name: string }>
  return graphs.find(g => g.name === `Kopie von ${originalName}`)?.id ?? null
}

// ── Hilfsfunktion: zur Logic-View navigieren und Graph laden ──────────────

async function gotoLogicWithGraph(page: any, graphId: string) {
  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  await page.selectOption('[data-testid="select-graph"]', graphId)
  // Warten bis loadGraph abgeschlossen (Button erscheint erst nach activeGraphId != '')
  await expect(page.locator('[data-testid="btn-duplicate"]')).toBeVisible({ timeout: 5_000 })
}

// ---------------------------------------------------------------------------
// Test 1: Duplizieren-Button ist sichtbar wenn Graph geladen
// ---------------------------------------------------------------------------

test('Logic: Duplizieren-Button erscheint wenn ein Graph aktiv ist', async ({ page }) => {
  const gid = await createGraphViaApi(`E2E-Dup-${Date.now()}`)
  try {
    await gotoLogicWithGraph(page, gid)
    await expect(page.locator('[data-testid="btn-duplicate"]')).toBeVisible()
  } finally {
    await deleteGraphViaApi(gid)
  }
})

// ---------------------------------------------------------------------------
// Test 2: Duplizieren erzeugt einen neuen Graph (via API-Verifikation)
// ---------------------------------------------------------------------------

test('Logic: Graph duplizieren erzeugt Kopie', async ({ page }) => {
  const name = `E2E-Orig-${Date.now()}`
  const gid  = await createGraphViaApi(name)
  let copyId: string | null = null

  try {
    await gotoLogicWithGraph(page, gid)
    await page.click('[data-testid="btn-duplicate"]')

    // Statusmeldung prüfen (erscheint im Status-Bar)
    await expect(page.locator('.bg-green-500\\/10')).toBeVisible({ timeout: 8_000 })

    // Per API verifizieren, dass die Kopie erstellt wurde
    copyId = await findCopyGraphId(name)
    expect(copyId).not.toBeNull()
  } finally {
    await deleteGraphViaApi(gid)
    if (copyId) await deleteGraphViaApi(copyId)
  }
})

// ---------------------------------------------------------------------------
// Test 3: Exportieren-Link ist vorhanden und zeigt auf korrekte URL
// ---------------------------------------------------------------------------

test('Logic: Exportieren-Link zeigt auf Export-Endpoint', async ({ page }) => {
  const gid = await createGraphViaApi(`E2E-Export-${Date.now()}`)
  try {
    await gotoLogicWithGraph(page, gid)
    const exportLink = page.locator('[data-testid="btn-export"]')
    await expect(exportLink).toBeVisible()
    const href = await exportLink.getAttribute('href')
    expect(href).toContain(`/api/v1/logic/graphs/${gid}/export`)
  } finally {
    await deleteGraphViaApi(gid)
  }
})

// ---------------------------------------------------------------------------
// Test 4: Buttons nicht sichtbar wenn kein Graph geladen
// ---------------------------------------------------------------------------

test('Logic: Duplizieren/Exportieren-Buttons sind ohne aktiven Graph nicht sichtbar', async ({ page }) => {
  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  // Kein Graph gewählt → v-if="activeGraphId" ist false
  await expect(page.locator('[data-testid="btn-duplicate"]')).not.toBeVisible()
  await expect(page.locator('[data-testid="btn-export"]')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 5: Importieren-Label ist immer sichtbar
// ---------------------------------------------------------------------------

test('Logic: Importieren-Button ist immer sichtbar', async ({ page }) => {
  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-testid="btn-import"]')).toBeVisible({ timeout: 5_000 })
})
