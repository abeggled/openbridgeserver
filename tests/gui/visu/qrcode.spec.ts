import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das QR-Code-Widget.
 *
 * Priorität hoch:
 *   1. Kein Inhalt konfiguriert → Platzhalter "QR-Code-Inhalt konfigurieren"
 *   2. URL konfiguriert → <svg>-Element wird in [data-testid="qrcode-svg"] gerendert
 *   3. Langer Text / URL → QR-Code wird trotzdem gerendert (kein Fehler)
 *
 * Priorität mittel:
 *   4. Label-Text wird angezeigt
 *   5. Kein img- oder video-Element gerendert (SVG-basiert)
 *   6. Fehlerkorrektur H → QR-Code gerendert (kein Absturz)
 */

// ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

async function createVisuPage() {
  return (await apiPost('/api/v1/visu/nodes', {
    name: `E2E-QrCode-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  })) as { id: string }
}

async function buildQrPage(
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
        name: 'E2E QR-Code',
        type: 'QrCode',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 4, h: 4,
        config,
      },
    ],
  })
}

// ─── Test 1 (hoch): Kein Inhalt → Platzhalter ────────────────────────────────

test('QrCode: kein Inhalt konfiguriert → Platzhalter sichtbar', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildQrPage(pageId, widgetId, {
    content: '',
    label: '',
    errorCorrection: 'M',
    darkColor: '#000000',
    lightColor: '#ffffff',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible()

    // Platzhalter-Text sichtbar
    await expect(widget.locator('[data-testid="qrcode-placeholder"]')).toBeVisible({ timeout: 5_000 })
    await expect(widget).toContainText('QR-Code-Inhalt konfigurieren')

    // Kein SVG-Container mit Inhalt
    await expect(widget.locator('[data-testid="qrcode-svg"]')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 2 (hoch): URL konfiguriert → SVG gerendert ─────────────────────────

test('QrCode: URL konfiguriert → <svg>-Element wird gerendert', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildQrPage(pageId, widgetId, {
    content: 'https://example.com',
    label: '',
    errorCorrection: 'M',
    darkColor: '#000000',
    lightColor: '#ffffff',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible()

    // SVG-Container sichtbar
    const svgContainer = widget.locator('[data-testid="qrcode-svg"]')
    await expect(svgContainer).toBeVisible({ timeout: 5_000 })

    // Echtes <svg>-Element im Container
    await expect(svgContainer.locator('svg')).toBeVisible({ timeout: 3_000 })

    // Kein Platzhalter, kein Fehler
    await expect(widget.locator('[data-testid="qrcode-placeholder"]')).toHaveCount(0)
    await expect(widget.locator('[data-testid="qrcode-error"]')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 3 (hoch): Langer Text → QR-Code gerendert ─────────────────────────

test('QrCode: langer Inhalt → QR-Code wird trotzdem gerendert', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()
  // WiFi-String + langer Pfad → mehr als 100 Zeichen
  const longContent = 'WIFI:S:MeinHeimNetzwerk;T:WPA2;P:SuperGeheimesPasswort2024!;H:false;;'

  await buildQrPage(pageId, widgetId, {
    content: longContent,
    label: 'WiFi scannen',
    errorCorrection: 'M',
    darkColor: '#000000',
    lightColor: '#ffffff',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    const svgContainer = widget.locator('[data-testid="qrcode-svg"]')
    await expect(svgContainer).toBeVisible({ timeout: 5_000 })
    await expect(svgContainer.locator('svg')).toBeVisible({ timeout: 3_000 })

    // Kein Fehler-Element
    await expect(widget.locator('[data-testid="qrcode-error"]')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 4 (mittel): Label-Text wird angezeigt ───────────────────────────────

test('QrCode: konfiguriertes Label wird angezeigt', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildQrPage(pageId, widgetId, {
    content: 'https://example.com',
    label: 'Haupt-Webseite',
    errorCorrection: 'M',
    darkColor: '#000000',
    lightColor: '#ffffff',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget.locator('[data-testid="qrcode-label"]')).toBeVisible({ timeout: 5_000 })
    await expect(widget.locator('[data-testid="qrcode-label"]')).toContainText('Haupt-Webseite')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 5 (mittel): Kein <img> oder <video> ────────────────────────────────

test('QrCode: kein <img>- oder <video>-Element (SVG-basiert)', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildQrPage(pageId, widgetId, {
    content: 'https://example.com',
    label: '',
    errorCorrection: 'M',
    darkColor: '#000000',
    lightColor: '#ffffff',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    // Sicherstellen dass das Widget geladen ist
    await expect(widget.locator('[data-testid="qrcode-svg"]')).toBeVisible({ timeout: 5_000 })

    // Kein img- oder video-Element
    await expect(widget.locator('img')).toHaveCount(0)
    await expect(widget.locator('video')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 6 (mittel): Fehlerkorrektur H → kein Fehler ────────────────────────

test('QrCode: Fehlerkorrektur H → QR-Code gerendert ohne Fehler', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildQrPage(pageId, widgetId, {
    content: 'https://example.com/test',
    label: '',
    errorCorrection: 'H',
    darkColor: '#000000',
    lightColor: '#ffffff',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    const svgContainer = widget.locator('[data-testid="qrcode-svg"]')
    await expect(svgContainer).toBeVisible({ timeout: 5_000 })
    await expect(svgContainer.locator('svg')).toBeVisible({ timeout: 3_000 })
    await expect(widget.locator('[data-testid="qrcode-error"]')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})
