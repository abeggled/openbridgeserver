import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das Energiefluss-Widget.
 *
 * Getestete Szenarien:
 *   1. Widget rendert ohne konfigurierte Knoten → Platzhaltertext sichtbar
 *   2. Werte erscheinen via WebSocket-Push (kW-Formatierung, Anzeigetext)
 *   3. Flussrichtung «Nur zum Haus» → Punkt bewegt sich immer Richtung Zentrum
 *      (keyPoints="0;1"), unabhängig vom Vorzeichen
 *   4. Flussrichtung «Nur vom Haus» → keyPoints="1;0" immer
 *   5. Flussrichtung «Bidirektional» → keyPoints hängt vom Vorzeichen ab
 */

// ─── Hilfsfunktionen ────────────────────────────────────────────────────────

async function createDataPoint(nameSuffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-EF-${nameSuffix}-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-EF-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushValue(dpId: string, value: number) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

interface EntityConfig {
  id: string
  label: string
  icon: string
  color: string
  direction: 'to_house' | 'from_house' | 'bidirectional'
  unit: string
  decimals: number
  invert: boolean
}

async function buildPage(
  pageId: string,
  widgetId: string,
  entities: EntityConfig[],
) {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id: widgetId,
        name: 'E2E Energiefluss',
        type: 'Energiefluss',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 6, h: 5,
        config: { label: 'E2E Test', entities },
      },
    ],
  })
}

// ─── Test 1: Leeres Widget zeigt Platzhalter ─────────────────────────────────

test('Energiefluss: leeres Widget zeigt Platzhaltertext', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible()
    await expect(widget).toContainText('Keine Energieknoten konfiguriert')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 2: Werte erscheinen via WebSocket ──────────────────────────────────

test('Energiefluss: Werte erscheinen via WebSocket (inkl. kW-Formatierung)', async ({ page }) => {
  const dp1 = await createDataPoint('pv')
  const dp2 = await createDataPoint('netz')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [
    { id: dp1.id, label: 'PV',   icon: '☀️', color: '#facc15',
      direction: 'to_house', unit: 'W', decimals: 1, invert: false },
    { id: dp2.id, label: 'Netz', icon: '⚡', color: '#60a5fa',
      direction: 'bidirectional', unit: 'W', decimals: 1, invert: false },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible()

    // Normaler Wert unter 1000 W
    await pushValue(dp1.id, 450)
    await expect(widget.locator('[data-testid="ef-value-0"]')).toContainText('450', { timeout: 3_000 })

    // Wert ≥ 1000 W → kW-Formatierung
    await pushValue(dp1.id, 3200)
    await expect(widget.locator('[data-testid="ef-value-0"]')).toContainText('kW', { timeout: 3_000 })

    // Zweiter Knoten
    await pushValue(dp2.id, -800)
    await expect(widget.locator('[data-testid="ef-value-1"]')).toContainText('800', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp1.id}`)
    await apiDelete(`/api/v1/datapoints/${dp2.id}`)
  }
})

// ─── Test 3–5: Flussrichtung → keyPoints-Attribut ───────────────────────────

test('Energiefluss: to_house → Punkt immer Richtung Haus (keyPoints 0;1)', async ({ page }) => {
  const dp = await createDataPoint('solar')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [
    { id: dp.id, label: 'PV', icon: '☀️', color: '#facc15',
      direction: 'to_house', unit: 'W', decimals: 1, invert: false },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    const dot = widget.locator('[data-testid="ef-dot-0"]')

    // Positiver Wert → to_house → keyPoints="0;1"
    await pushValue(dp.id, 2000)
    await expect(dot).toBeVisible({ timeout: 3_000 })
    const kpPos = await dot.locator('animateMotion').getAttribute('keyPoints')
    expect(kpPos).toBe('0;1')

    // Negativer Wert → to_house bleibt trotzdem "0;1"
    await pushValue(dp.id, -500)
    // Wert auf aktiv halten (|value| > 1)
    const kpNeg = await dot.locator('animateMotion').getAttribute('keyPoints')
    expect(kpNeg).toBe('0;1')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

test('Energiefluss: from_house → Punkt immer vom Haus weg (keyPoints 1;0)', async ({ page }) => {
  const dp = await createDataPoint('wallbox')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [
    { id: dp.id, label: 'Wallbox', icon: '🚗', color: '#34d399',
      direction: 'from_house', unit: 'W', decimals: 1, invert: false },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    const dot = widget.locator('[data-testid="ef-dot-0"]')

    await pushValue(dp.id, 1100)
    await expect(dot).toBeVisible({ timeout: 3_000 })
    const kp = await dot.locator('animateMotion').getAttribute('keyPoints')
    expect(kp).toBe('1;0')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

test('Energiefluss: bidirektional → keyPoints folgt dem Vorzeichen', async ({ page }) => {
  const dp = await createDataPoint('batterie')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, [
    { id: dp.id, label: 'Batterie', icon: '🔋', color: '#a78bfa',
      direction: 'bidirectional', unit: 'W', decimals: 1, invert: false },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    const dot = widget.locator('[data-testid="ef-dot-0"]')

    // Positiv → Energie fliesst zum Haus → "0;1"
    await pushValue(dp.id, 1500)
    await expect(dot).toBeVisible({ timeout: 3_000 })
    const kpPos = await dot.locator('animateMotion').getAttribute('keyPoints')
    expect(kpPos).toBe('0;1')

    // Negativ → Energie fliesst vom Haus → "1;0"
    await pushValue(dp.id, -1500)
    await page.waitForTimeout(500)  // kurz warten bis Vue re-rendert
    const kpNeg = await dot.locator('animateMotion').getAttribute('keyPoints')
    expect(kpNeg).toBe('1;0')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
