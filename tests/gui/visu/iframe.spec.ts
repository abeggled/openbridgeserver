import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das iFrame-Widget.
 *
 * Priorität hoch:
 *   1. Kein URL konfiguriert → Platzhalter "Keine URL konfiguriert"
 *   2. URL konfiguriert → <iframe> mit korrektem src-Attribut
 *   3. Sandbox-Attribut wird korrekt gesetzt
 *   4. allowfullscreen-Attribut bei aktiviertem Vollbild
 *
 * Priorität mittel:
 *   5. Label-Text wird in der Kopfzeile angezeigt
 *   6. Kein sandbox-Attribut wenn Sandbox-String leer
 */

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-IFrame-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function buildIFramePage(
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
        id: widgetId,
        name: 'E2E iFrame',
        type: 'IFrame',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 6, h: 4,
        config,
      },
    ],
  })
}

// ─── Test 1 (hoch): Kein URL → Platzhalter ───────────────────────────────────

test('iFrame: kein URL konfiguriert → Platzhalter "Keine URL konfiguriert"', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildIFramePage(pageId, widgetId, { label: '', url: '' })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toContainText('Keine URL konfiguriert')
    await expect(widget.locator('iframe')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 2 (hoch): URL gesetzt → <iframe> mit korrektem src ─────────────────

test('iFrame: URL konfiguriert → <iframe> mit korrektem src-Attribut', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()
  const frameUrl = 'https://example.com'

  await buildIFramePage(pageId, widgetId, {
    url: frameUrl,
    sandbox: 'allow-same-origin allow-scripts',
    allowFullscreen: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const iframe = page.locator(`[data-widget-id="${widgetId}"] iframe`)
    await expect(iframe).toBeAttached({ timeout: 10_000 })
    await expect(iframe).toHaveAttribute('src', frameUrl)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 3 (hoch): Sandbox-Attribut gesetzt ─────────────────────────────────

test('iFrame: Sandbox-Permissions werden als sandbox-Attribut am <iframe> gesetzt', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()
  const sandbox  = 'allow-same-origin allow-scripts allow-popups'

  await buildIFramePage(pageId, widgetId, {
    url: 'https://example.com',
    sandbox,
    allowFullscreen: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const iframe = page.locator(`[data-widget-id="${widgetId}"] iframe`)
    await expect(iframe).toBeAttached({ timeout: 10_000 })
    await expect(iframe).toHaveAttribute('sandbox', sandbox)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 4 (hoch): allowfullscreen-Attribut ─────────────────────────────────

test('iFrame: allowFullscreen=true → allowfullscreen-Attribut am <iframe> vorhanden', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildIFramePage(pageId, widgetId, {
    url: 'https://example.com',
    sandbox: 'allow-same-origin allow-scripts',
    allowFullscreen: true,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const iframe = page.locator(`[data-widget-id="${widgetId}"] iframe`)
    await expect(iframe).toBeAttached({ timeout: 10_000 })
    // allowfullscreen ist ein Boolean-Attribut – prüfen dass es gesetzt ist
    const val = await iframe.getAttribute('allowfullscreen')
    expect(val).not.toBeNull()
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 5 (mittel): Label-Text ─────────────────────────────────────────────

test('iFrame: konfiguriertes Label wird in Kopfzeile angezeigt', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildIFramePage(pageId, widgetId, {
    label: 'Mein Dashboard',
    url: 'https://example.com',
    sandbox: 'allow-same-origin allow-scripts',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toContainText('Mein Dashboard')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 6 (mittel): Leerer Sandbox-String → kein sandbox-Attribut ──────────

test('iFrame: leerer Sandbox-String → kein sandbox-Attribut am <iframe>', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildIFramePage(pageId, widgetId, {
    url: 'https://example.com',
    sandbox: '',
    allowFullscreen: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const iframe = page.locator(`[data-widget-id="${widgetId}"] iframe`)
    await expect(iframe).toBeAttached({ timeout: 10_000 })
    const sandboxVal = await iframe.getAttribute('sandbox')
    expect(sandboxVal).toBeNull()
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})
