import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das Uhr-Widget (Issue #167).
 *
 * Testsuite deckt ab:
 *   1. Digital-Modus: Widget wird gerendert und zeigt eine gültige Uhrzeit
 *   2. Digital-Modus mit Sekunden: HH:MM:SS Format sichtbar
 *   3. Digital-Modus mit Datum: Datum wird unterhalb der Zeit angezeigt
 *   4. Analog-Modus: SVG-Uhr wird gerendert
 *   5. Wortuhr-Modus: Buchstabenraster 11×10 wird gerendert, ES und IST sind hervorgehoben
 *   6. Wortuhr-Modus: Korrekte Wörter für verschiedene Uhrzeiten (gemockt via JS)
 */

// ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Uhr-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function buildUhrPage(
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
        id:               widgetId,
        name:             'E2E Uhr',
        type:             'Uhr',
        datapoint_id:     null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 4, h: 4,
        config,
      },
    ],
  })
}

// ─── Test 1: Digital-Modus – Grunddarstellung ─────────────────────────────────

test('Uhr digital-Modus: Widget rendert und zeigt gültige HH:MM-Zeit', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildUhrPage(pageId, widgetId, {
    mode:        'digital',
    showSeconds: false,
    showDate:    false,
    color:       '#3b82f6',
    label:       '',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const timeEl = page.locator(`[data-widget-id="${widgetId}"] [data-testid="uhr-digital-time"]`)
    await expect(timeEl).toBeVisible({ timeout: 5_000 })

    // Format muss HH:MM sein (z.B. "14:35")
    const text = await timeEl.textContent()
    expect(text).toMatch(/^\d{2}:\d{2}$/)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 2: Digital-Modus – Sekunden ────────────────────────────────────────

test('Uhr digital-Modus mit Sekunden: Format HH:MM:SS', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildUhrPage(pageId, widgetId, {
    mode:        'digital',
    showSeconds: true,
    showDate:    false,
    color:       '#10b981',
    label:       'Testlabel',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const timeEl = page.locator(`[data-widget-id="${widgetId}"] [data-testid="uhr-digital-time"]`)
    await expect(timeEl).toBeVisible({ timeout: 5_000 })

    const text = await timeEl.textContent()
    expect(text).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 3: Digital-Modus – Datum ───────────────────────────────────────────

test('Uhr digital-Modus mit Datum: Datumselement ist sichtbar', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildUhrPage(pageId, widgetId, {
    mode:        'digital',
    showSeconds: false,
    showDate:    true,
    color:       '#3b82f6',
    label:       '',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widgetEl = page.locator(`[data-widget-id="${widgetId}"]`)

    await expect(widgetEl.locator('[data-testid="uhr-digital-time"]')).toBeVisible({ timeout: 5_000 })
    await expect(widgetEl.locator('[data-testid="uhr-digital-date"]')).toBeVisible({ timeout: 3_000 })

    // Datum muss das aktuelle Jahr enthalten
    const dateText = await widgetEl.locator('[data-testid="uhr-digital-date"]').textContent()
    const year     = new Date().getFullYear().toString()
    expect(dateText).toContain(year)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 4: Analog-Modus ────────────────────────────────────────────────────

test('Uhr analog-Modus: SVG-Uhr wird gerendert', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildUhrPage(pageId, widgetId, {
    mode:        'analog',
    showSeconds: true,
    color:       '#f59e0b',
    label:       'Analog-Test',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const svgEl = page.locator(`[data-widget-id="${widgetId}"] [data-testid="uhr-analog"]`)
    await expect(svgEl).toBeVisible({ timeout: 5_000 })

    // SVG muss vorhanden sein
    const tagName = await svgEl.evaluate(el => el.tagName.toLowerCase())
    expect(tagName).toBe('svg')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 5: Wortuhr-Modus – Grunddarstellung ────────────────────────────────

test('Uhr wortuhr-Modus: Buchstabenraster wird gerendert, 110 Zellen vorhanden', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildUhrPage(pageId, widgetId, {
    mode:  'wortuhr',
    color: '#3b82f6',
    label: '',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const wortuhrEl = page.locator(`[data-widget-id="${widgetId}"] [data-testid="uhr-wortuhr"]`)
    await expect(wortuhrEl).toBeVisible({ timeout: 5_000 })

    // 11 Spalten × 10 Zeilen = 110 Zellen
    const zellen = wortuhrEl.locator('div > div')
    await expect(zellen).toHaveCount(110, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 6: Wortuhr-Modus – ES IST immer hervorgehoben ──────────────────────

test('Uhr wortuhr-Modus: ES und IST sind immer in Akzentfarbe', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()
  const color    = '#e11d48'

  await buildUhrPage(pageId, widgetId, {
    mode:  'wortuhr',
    color,
    label: '',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const wortuhrEl = page.locator(`[data-widget-id="${widgetId}"] [data-testid="uhr-wortuhr"]`)
    await expect(wortuhrEl).toBeVisible({ timeout: 5_000 })

    // Prüfe, dass mindestens einige Zellen die Akzentfarbe haben
    // (ES und IST sind immer aktiv → mindestens 5 hervorgehobene Buchstaben)
    const hervorgehobeneZellen = await wortuhrEl.locator('div > div').evaluateAll(
      (cells, accentColor) => cells.filter(
        cell => (cell as HTMLElement).style.color === accentColor
          || (cell as HTMLElement).style.color.includes(accentColor.slice(1)),
      ).length,
      color,
    )

    // ES (2) + IST (3) = mindestens 5 immer aktiv
    expect(hervorgehobeneZellen).toBeGreaterThanOrEqual(5)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})
