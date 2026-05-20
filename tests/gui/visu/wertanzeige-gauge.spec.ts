import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für den Gauge-Modus des Wertanzeige-Widgets (Issue #416).
 *
 * Testsuite deckt ab:
 *   1. Gauge Bogen: Wert wird korrekt im SVG angezeigt
 *   2. Gauge Kreis: Wert wird korrekt im SVG angezeigt
 *   3. Gauge Bogen: Arc füllt sich mit steigendem Wert (Dashoffset verringert sich)
 *   4. Gauge Bogen: Grenzwerte (min/max) werden korrekt skaliert
 */

// ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

async function createFloatDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-Gauge-${suffix}-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Gauge-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushFloat(dpId: string, value: number) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

async function buildGaugePage(
  pageId: string,
  widgetId: string,
  dpId: string,
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
        name: 'E2E Gauge',
        type: 'ValueDisplay',
        datapoint_id: dpId,
        status_datapoint_id: null,
        x: 0, y: 0, w: 3, h: 3,
        config,
      },
    ],
  })
}

const DEFAULT_RULES = [
  {
    fn: 'default',
    threshold: '',
    icon: '',
    color: '#6b7280',
    output_type: 'value',
    calculation: '',
    prefix: '',
    text: '',
    decimals: 1,
    postfix: '°C',
  },
]

// ─── Test 1: Gauge Bogen – Wert wird angezeigt ────────────────────────────────

test('Gauge Bogen: aktueller Wert wird im SVG-Text angezeigt', async ({ page }) => {
  const dp       = await createFloatDP('arc-value')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildGaugePage(pageId, widgetId, dp.id, {
    label: 'Temperatur',
    mode: 'gauge_arc',
    gauge_min: 0,
    gauge_max: 100,
    gauge_colors: ['#22c55e', '#f59e0b', '#ef4444'],
    rules: DEFAULT_RULES,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const valueEl = page.locator(`[data-widget-id="${widgetId}"] [data-testid="widget-value"]`)

    await pushFloat(dp.id, 42.5)
    await expect(valueEl).toHaveText('42.5', { timeout: 3_000 })

    await pushFloat(dp.id, 0)
    await expect(valueEl).toHaveText('0.0', { timeout: 3_000 })

    await pushFloat(dp.id, 100)
    await expect(valueEl).toHaveText('100.0', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2: Gauge Kreis – Wert wird angezeigt ───────────────────────────────

test('Gauge Kreis: aktueller Wert wird im SVG-Text angezeigt', async ({ page }) => {
  const dp       = await createFloatDP('circle-value')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildGaugePage(pageId, widgetId, dp.id, {
    label: 'Feuchtigkeit',
    mode: 'gauge_circle',
    gauge_min: 0,
    gauge_max: 100,
    gauge_colors: ['#3b82f6', '#22c55e'],
    rules: [
      {
        fn: 'default',
        threshold: '',
        icon: '',
        color: '#6b7280',
        output_type: 'value',
        calculation: '',
        prefix: '',
        text: '',
        decimals: 0,
        postfix: '%',
      },
    ],
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const valueEl = page.locator(`[data-widget-id="${widgetId}"] [data-testid="widget-value"]`)

    await pushFloat(dp.id, 75)
    await expect(valueEl).toHaveText('75', { timeout: 3_000 })

    await pushFloat(dp.id, 0)
    await expect(valueEl).toHaveText('0', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 3: Gauge Bogen – Dashoffset verringert sich mit steigendem Wert ─────

test('Gauge Bogen: stroke-dashoffset verringert sich wenn Wert steigt', async ({ page }) => {
  const dp       = await createFloatDP('arc-fill')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildGaugePage(pageId, widgetId, dp.id, {
    label: 'Test',
    mode: 'gauge_arc',
    gauge_min: 0,
    gauge_max: 100,
    gauge_colors: ['#22c55e', '#ef4444'],
    rules: [
      {
        fn: 'default',
        threshold: '',
        icon: '',
        color: '#6b7280',
        output_type: 'value',
        calculation: '',
        prefix: '',
        text: '',
        decimals: 0,
        postfix: '',
      },
    ],
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const arc = page.locator(`[data-widget-id="${widgetId}"] [data-testid="gauge-arc-fill"]`)

    await pushFloat(dp.id, 0)
    await page.waitForTimeout(600)
    const offset0 = parseFloat((await arc.getAttribute('stroke-dashoffset')) ?? '0')

    await pushFloat(dp.id, 50)
    await page.waitForTimeout(600)
    const offset50 = parseFloat((await arc.getAttribute('stroke-dashoffset')) ?? '0')

    await pushFloat(dp.id, 100)
    await page.waitForTimeout(600)
    const offset100 = parseFloat((await arc.getAttribute('stroke-dashoffset')) ?? '0')

    // Bei höherem Wert → kleinerer Dashoffset → mehr Arc sichtbar
    expect(offset0).toBeGreaterThan(offset50)
    expect(offset50).toBeGreaterThan(offset100)
    expect(offset100).toBeCloseTo(0, 1)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 4: Gauge Bogen – Skalierung min/max ─────────────────────────────────

test('Gauge Bogen: Wert ausserhalb min/max wird auf 0% bzw. 100% begrenzt', async ({ page }) => {
  const dp       = await createFloatDP('arc-clamp')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildGaugePage(pageId, widgetId, dp.id, {
    label: 'Druck',
    mode: 'gauge_arc',
    gauge_min: 10,
    gauge_max: 90,
    gauge_colors: ['#22c55e', '#ef4444'],
    rules: [
      {
        fn: 'default',
        threshold: '',
        icon: '',
        color: '#6b7280',
        output_type: 'value',
        calculation: '',
        prefix: '',
        text: '',
        decimals: 1,
        postfix: '',
      },
    ],
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const arc = page.locator(`[data-widget-id="${widgetId}"] [data-testid="gauge-arc-fill"]`)

    // Wert unterhalb minimum → dashoffset = volle Arc-Länge (0% anzeige)
    await pushFloat(dp.id, 0)
    await page.waitForTimeout(600)
    const offsetUnder = parseFloat((await arc.getAttribute('stroke-dashoffset')) ?? '0')

    // Wert oberhalb maximum → dashoffset ≈ 0 (100% anzeige)
    await pushFloat(dp.id, 999)
    await page.waitForTimeout(600)
    const offsetOver = parseFloat((await arc.getAttribute('stroke-dashoffset')) ?? '0')

    // Wert unterhalb min: 0% → kein Arc sichtbar (grosser Offset)
    expect(offsetUnder).toBeGreaterThan(100)
    // Wert oberhalb max: 100% → fast vollständig (Offset nahe 0)
    expect(offsetOver).toBeCloseTo(0, 1)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
