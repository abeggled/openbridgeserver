import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das Balkendiagramm im Verlaufs-Widget (Issue #418).
 *
 * Getestete Szenarien:
 *   1. chart_type='bar' rendert Canvas ohne Fehler
 *   2. Wechsel von Linie auf Balken (config-Update) zerstört Chart nicht
 *   3. Balkendiagramm mit zwei Reihen bleibt stabil
 */

async function createFloatDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-Bar-${suffix}-${Date.now()}`,
    data_type: 'FLOAT',
    unit: '°C',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Bar-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushValue(dpId: string, value: number) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

async function buildBarChartPage(
  pageId: string,
  widgetId: string,
  primaryDpId: string,
  chartType: 'line' | 'bar',
  series: Array<{ dp_id: string; label: string; color: string }> = [],
) {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id: widgetId,
        name: 'E2E Bar Chart Widget',
        type: 'Chart',
        datapoint_id: primaryDpId,
        status_datapoint_id: null,
        x: 0, y: 0, w: 6, h: 4,
        config: { label: 'E2E Balken', hours: 24, chart_type: chartType, series },
      },
    ],
  })
}

// ─── Test 1: Balkendiagramm rendert Canvas korrekt ───────────────────────────

test('Verlauf-Widget: Balkendiagramm rendert Canvas korrekt', async ({ page }) => {
  const dp = await createFloatDP('bar-single')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 22.5)
  await buildBarChartPage(pageId, widgetId, dp.id, 'bar')

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    await page.goto(`/visu/${pageId}`)
    const canvas = page.locator('canvas').first()
    await expect(canvas).toBeVisible({ timeout: 8000 })

    // Canvas muss Pixel enthalten
    const hasContent = await canvas.evaluate((el: HTMLCanvasElement) => {
      const ctx = el.getContext('2d')
      if (!ctx) return false
      const data = ctx.getImageData(0, 0, el.width, el.height).data
      return data.some(v => v > 0)
    })
    expect(hasContent).toBe(true)

    // Kein JavaScript-Fehler aufgetreten
    const chartErrors = errors.filter(e => e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'))
    expect(chartErrors).toHaveLength(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2: Liniendiagramm (Standard) und Balken rendern beide korrekt ───────

test('Verlauf-Widget: Linien- und Balkendiagramm rendern ohne Fehler', async ({ page }) => {
  const dp = await createFloatDP('line-and-bar')
  const visuNodeLine = await createVisuPage()
  const visuNodeBar  = await createVisuPage()
  const linePageId   = visuNodeLine.id
  const barPageId    = visuNodeBar.id
  const lineWidgetId = randomUUID()
  const barWidgetId  = randomUUID()

  await pushValue(dp.id, 18.3)
  await buildBarChartPage(linePageId, lineWidgetId, dp.id, 'line')
  await buildBarChartPage(barPageId,  barWidgetId,  dp.id, 'bar')

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    // Liniendiagramm prüfen
    await page.goto(`/visu/${linePageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    // Balkendiagramm prüfen
    await page.goto(`/visu/${barPageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    const chartErrors = errors.filter(e => e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'))
    expect(chartErrors).toHaveLength(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${linePageId}`)
    await apiDelete(`/api/v1/visu/nodes/${barPageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 3: Balkendiagramm mit zwei Reihen ──────────────────────────────────

test('Verlauf-Widget: Balkendiagramm mit zwei Reihen bleibt stabil', async ({ page }) => {
  const dp1 = await createFloatDP('bar-primary')
  const dp2 = await createFloatDP('bar-series1')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp1.id, 21.0)
  await pushValue(dp2.id, 35.0)

  await buildBarChartPage(pageId, widgetId, dp1.id, 'bar', [
    { dp_id: dp2.id, label: 'Außen', color: '#ef4444' },
  ])

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    await page.goto(`/visu/${pageId}`)
    const canvas = page.locator('canvas').first()
    await expect(canvas).toBeVisible({ timeout: 8000 })

    await expect(page.getByText('E2E Balken')).toBeVisible()

    const chartErrors = errors.filter(e => e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'))
    expect(chartErrors).toHaveLength(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp1.id}`)
    await apiDelete(`/api/v1/datapoints/${dp2.id}`)
  }
})
