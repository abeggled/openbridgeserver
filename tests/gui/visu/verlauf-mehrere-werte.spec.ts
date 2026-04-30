import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das erweiterte Verlaufs-Widget (Issue #234 — mehrere Werte im Verlauf).
 *
 * Getestete Szenarien:
 *   1. Einzelne Reihe (Rückwärtskompatibilität): Canvas wird korrekt gerendert
 *   2. Zwei Reihen: Canvas wird mit mehreren Datensätzen gerendert, kein Fehler
 *   3. Drei Reihen (Primär + 2 konfigurierte): Widget bleibt stabil
 *   4. Widget ohne konfigurierten Datenpunkt aber mit Serie in Config: kein Absturz
 */

// ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

async function createFloatDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-ChartMulti-${suffix}-${Date.now()}`,
    data_type: 'FLOAT',
    unit: '°C',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-ChartMulti-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushValue(dpId: string, value: number) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

interface SeriesEntry { dp_id: string; label: string; color: string }

async function buildChartPage(
  pageId: string,
  widgetId: string,
  primaryDpId: string | null,
  series: SeriesEntry[],
  label = 'E2E Verlauf',
) {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id: widgetId,
        name: 'E2E Chart Widget',
        type: 'Chart',
        datapoint_id: primaryDpId,
        status_datapoint_id: null,
        x: 0, y: 0, w: 6, h: 4,
        config: { label, hours: 24, series },
      },
    ],
  })
}

// ─── Test 1: Einzelne Reihe — Rückwärtskompatibilität ────────────────────────

test('Verlauf-Widget: einzelne Reihe rendert Canvas korrekt', async ({ page }) => {
  const dp = await createFloatDP('single')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 22.5)
  await buildChartPage(pageId, widgetId, dp.id, [], 'Innen')

  try {
    await page.goto(`/visu/${pageId}`)
    const canvas = page.locator('canvas').first()
    await expect(canvas).toBeVisible({ timeout: 8000 })

    // Canvas muss Pixel enthalten (nicht leer)
    const hasContent = await canvas.evaluate((el: HTMLCanvasElement) => {
      const ctx = el.getContext('2d')
      if (!ctx) return false
      const data = ctx.getImageData(0, 0, el.width, el.height).data
      return data.some(v => v > 0)
    })
    expect(hasContent).toBe(true)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2: Zwei Reihen — Neue Multi-Series-Funktion ────────────────────────

test('Verlauf-Widget: zwei Reihen rendern ohne Fehler', async ({ page }) => {
  const dp1 = await createFloatDP('multi-primary')
  const dp2 = await createFloatDP('multi-series1')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp1.id, 21.0)
  await pushValue(dp2.id, 35.0)

  await buildChartPage(pageId, widgetId, dp1.id, [
    { dp_id: dp2.id, label: 'Außen', color: '#ef4444' },
  ], 'Temperaturen')

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    await page.goto(`/visu/${pageId}`)
    const canvas = page.locator('canvas').first()
    await expect(canvas).toBeVisible({ timeout: 8000 })

    // Widget-Label wird angezeigt
    await expect(page.getByText('Temperaturen')).toBeVisible()

    // Kein JavaScript-Fehler aufgetreten
    const chartErrors = errors.filter(e => e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'))
    expect(chartErrors).toHaveLength(0)

    // Canvas hat Inhalt
    const hasContent = await canvas.evaluate((el: HTMLCanvasElement) => {
      const ctx = el.getContext('2d')
      if (!ctx) return false
      const data = ctx.getImageData(0, 0, el.width, el.height).data
      return data.some(v => v > 0)
    })
    expect(hasContent).toBe(true)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp1.id}`)
    await apiDelete(`/api/v1/datapoints/${dp2.id}`)
  }
})

// ─── Test 3: Drei Reihen — Primär + 2 konfigurierte Serien ───────────────────

test('Verlauf-Widget: drei Reihen (Primär + 2 konfigurierte) bleiben stabil', async ({ page }) => {
  const dp1 = await createFloatDP('tri-primary')
  const dp2 = await createFloatDP('tri-series1')
  const dp3 = await createFloatDP('tri-series2')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp1.id, 20.0)
  await pushValue(dp2.id, 30.0)
  await pushValue(dp3.id, 40.0)

  await buildChartPage(pageId, widgetId, dp1.id, [
    { dp_id: dp2.id, label: 'Reihe 2', color: '#10b981' },
    { dp_id: dp3.id, label: 'Reihe 3', color: '#f59e0b' },
  ], 'Drei Reihen')

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    const chartErrors = errors.filter(e => e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'))
    expect(chartErrors).toHaveLength(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp1.id}`)
    await apiDelete(`/api/v1/datapoints/${dp2.id}`)
    await apiDelete(`/api/v1/datapoints/${dp3.id}`)
  }
})

// ─── Test 4: Nur konfigurierte Serie (ohne Primär-DP) ────────────────────────

test('Verlauf-Widget: nur konfigurierte Serie ohne Primär-DP lädt ohne Absturz', async ({ page }) => {
  const dp = await createFloatDP('config-only')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 99.9)

  // Primär-DP leer, Datenpunkt nur in config.series
  await buildChartPage(pageId, widgetId, null, [
    { dp_id: dp.id, label: 'Nur Serie', color: '#8b5cf6' },
  ], 'Nur Serie')

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    await page.goto(`/visu/${pageId}`)
    // Canvas muss sichtbar sein, aber auch ohne primary DP funktionieren
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    const chartErrors = errors.filter(e => e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'))
    expect(chartErrors).toHaveLength(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
