import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das Balkenanzeige (Horizontal) Widget (Issue #417).
 *
 * Getestete Szenarien:
 *   1. Widget ohne Balken → Platzhaltertext sichtbar
 *   2. Datenpunkt-Wert aktualisiert Wertanzeige korrekt
 *   3. Balken-Füllung wächst proportional (0 % bei Min, ~100 % bei Max)
 *   4. Mehrere Balken zeigen unabhängige Werte
 */

async function createFloatDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-HBar-${suffix}-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-HBar-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushValue(dpId: string, value: number) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

interface BarCfg {
  label: string
  dp_id: string
  min: number
  max: number
  decimals: number
  prefix: string
  postfix: string
}

async function buildPage(
  pageId: string,
  widgetId: string,
  bars: BarCfg[],
  extra?: Record<string, unknown>,
) {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id: widgetId,
        name: 'E2E Balkenanzeige',
        type: 'HorizontalBar',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 6, h: 3,
        config: {
          label: 'Test Balken',
          bars,
          colors: ['#22c55e', '#ef4444'],
          show_value: true,
          ...extra,
        },
      },
    ],
  })
}

// ─── Test 1: Leeres Widget → Platzhalter ─────────────────────────────────────

test('HorizontalBar: kein Balken konfiguriert → Platzhaltertext sichtbar', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toContainText('Keine Balken konfiguriert', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 2: Wertanzeige aktualisiert sich bei Datenpunkt-Push ────────────────

test('HorizontalBar: Datenpunkt-Wert erscheint als formatierter Text', async ({ page }) => {
  const dp       = await createFloatDP('value')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [
    { label: 'Raum A', dp_id: dp.id, min: 0, max: 100, decimals: 1, prefix: '', postfix: '°C' },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const valueEl = page.locator(`[data-widget-id="${widgetId}"] [data-testid="widget-value"]`)

    await pushValue(dp.id, 23.5)
    await expect(valueEl).toHaveText('23.5 °C', { timeout: 5_000 })

    await pushValue(dp.id, 0)
    await expect(valueEl).toHaveText('0.0 °C', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 3: Balken-Füllung 0 % bei Min-Wert, nahe 100 % bei Max-Wert ────────

test('HorizontalBar: Balkenfüllung 0 % bei Min, ≥ 95 % bei Max', async ({ page }) => {
  const dp       = await createFloatDP('fill')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [
    { label: 'Pegel', dp_id: dp.id, min: 0, max: 100, decimals: 0, prefix: '', postfix: '%' },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const fill = page.locator(`[data-widget-id="${widgetId}"] [data-testid="bar-fill"]`).first()

    // Bei 0 → Breite 0 %
    await pushValue(dp.id, 0)
    await page.waitForTimeout(800)
    const widthAt0 = await fill.evaluate((el) => parseFloat((el as HTMLElement).style.width))
    expect(widthAt0).toBeLessThanOrEqual(1)

    // Bei 100 → Breite ≥ 95 %
    await pushValue(dp.id, 100)
    await page.waitForTimeout(800)
    const widthAt100 = await fill.evaluate((el) => parseFloat((el as HTMLElement).style.width))
    expect(widthAt100).toBeGreaterThanOrEqual(95)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 4: Mehrere Balken → unabhängige Werte ───────────────────────────────

test('HorizontalBar: mehrere Balken zeigen unabhängige Werte', async ({ page }) => {
  const dp1      = await createFloatDP('multi-1')
  const dp2      = await createFloatDP('multi-2')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [
    { label: 'A', dp_id: dp1.id, min: 0, max: 100, decimals: 1, prefix: '', postfix: '' },
    { label: 'B', dp_id: dp2.id, min: 0, max: 50, decimals: 1, prefix: '', postfix: '' },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    await pushValue(dp1.id, 75)
    await pushValue(dp2.id, 25)

    const values = page.locator(`[data-widget-id="${widgetId}"] [data-testid="widget-value"]`)
    await expect(values.nth(0)).toHaveText('75.0', { timeout: 5_000 })
    await expect(values.nth(1)).toHaveText('25.0', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp1.id}`)
    await apiDelete(`/api/v1/datapoints/${dp2.id}`)
  }
})
