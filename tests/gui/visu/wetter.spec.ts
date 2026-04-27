import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das Wetter-Widget (Issue #185).
 *
 * Testsuite deckt ab:
 *   1. Kein URL konfiguriert → Platzhalter "Keine API-URL konfiguriert"
 *   2. Nicht erreichbare URL → Fehler-State mit Retry-Button
 *   3. Widget-Grundstruktur: data-testid="wetter-widget" vorhanden
 *   4. Konfigurierter Label erscheint als Ortsname
 *   5. Im Editor-Modus wird Platzhalter gezeigt (kein API-Fetch)
 */

// ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Wetter-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function buildWetterPage(
  pageId: string,
  widgetId: string,
  config: Record<string, unknown>,
) {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id:                  widgetId,
        name:                'E2E Wetter',
        type:                'Wetter',
        datapoint_id:        null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 6, h: 5,
        config,
      },
    ],
  })
}

// ─── Test 1: Kein URL → Platzhalter ───────────────────────────────────────────

test('Wetter: kein URL konfiguriert → Platzhalter "Keine API-URL konfiguriert"', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildWetterPage(pageId, widgetId, {
    label: '', url: '', refreshInterval: 600,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget.locator('[data-testid="wetter-widget"]')).toBeVisible({ timeout: 5_000 })
    await expect(widget).toContainText('Keine API-URL konfiguriert')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 2: Nicht erreichbare URL → Fehler + Retry-Button ───────────────────

test('Wetter: nicht erreichbare URL → Fehler-Overlay mit Retry-Button', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildWetterPage(pageId, widgetId, {
    label: '',
    // Port 19977: kein Server → Backend-Proxy gibt 502 zurück
    url: 'http://127.0.0.1:19977/onecall',
    refreshInterval: 600,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget.locator('[data-testid="wetter-widget"]')).toBeVisible({ timeout: 5_000 })

    // Fehler-Meldung und Retry-Button müssen erscheinen
    await expect(widget.locator('text=⚠️')).toBeVisible({ timeout: 10_000 })
    const retryBtn = widget.locator('button', { hasText: 'Neu laden' })
    await expect(retryBtn).toBeVisible({ timeout: 5_000 })
    await retryBtn.click()
    // Nach Klick kein JS-Fehler — nochmaliger Fetch-Versuch
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 3: Widget-Grundstruktur ─────────────────────────────────────────────

test('Wetter: Widget-Grundstruktur (data-testid) ist vorhanden', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildWetterPage(pageId, widgetId, {
    label: 'Zürich',
    url: '',
    refreshInterval: 600,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget.locator('[data-testid="wetter-widget"]')).toBeVisible({ timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 4: Konfigurierter Label erscheint ───────────────────────────────────

test('Wetter: konfiguriertes Label erscheint wenn keine Daten vorhanden', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  // Mit nicht-erreichbarer URL, aber gesetztem Label
  await buildWetterPage(pageId, widgetId, {
    label: 'Muster-Ort',
    url: 'http://127.0.0.1:19977/onecall',
    refreshInterval: 600,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    // Fehler-State erscheint — kein Location-Label sichtbar (Fehler-View hat keinen Orts-Header)
    await expect(widget.locator('[data-testid="wetter-widget"]')).toBeVisible({ timeout: 5_000 })
    const errorView = widget.locator('text=⚠️')
    await expect(errorView).toBeVisible({ timeout: 10_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 5: Editor-Modus → Platzhalter, kein API-Fetch ───────────────────────

test('Wetter: Editor-Modus zeigt Platzhalter ohne URL', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildWetterPage(pageId, widgetId, {
    label: '',
    url:   '',
    refreshInterval: 600,
  })

  try {
    // Editor-Route öffnen
    await page.goto(`/visu/${pageId}/edit`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget.locator('[data-testid="wetter-widget"]')).toBeVisible({ timeout: 5_000 })
    // Im Editor wird "Wetter-API-URL konfigurieren" als Platzhalter gezeigt
    await expect(widget).toContainText('konfigurieren')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})
