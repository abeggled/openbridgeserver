import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das Verlaufs-Widget mit variablem Zeitbereich (Issue #413).
 *
 * Getestete Szenarien:
 *   1. Standard-Zeitbereich aus Config wird im Dropdown angezeigt
 *   2. Dropdown enthält alle 18 Zeitbereich-Optionen
 *   3. Auswahl eines anderen Zeitbereichs aktualisiert das Chart (kein Absturz)
 *   4. Rückwärtskompatibilität: Widget ohne time_range (nur hours) zeigt last_24h
 */

// ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

async function createFloatDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-ChartZeit-${suffix}-${Date.now()}`,
    data_type: 'FLOAT',
    unit: '°C',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-ChartZeit-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushValue(dpId: string, value: number) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

async function buildChartPage(
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
        name: 'E2E Chart Zeitbereich',
        type: 'Chart',
        datapoint_id: dpId,
        status_datapoint_id: null,
        x: 0, y: 0, w: 8, h: 4,
        config,
      },
    ],
  })
}

// ─── Test 1: Konfigurierter Standard-Zeitbereich wird im Dropdown angezeigt ──

test('Verlauf-Widget: Standard-Zeitbereich "Letzte 1 Stunde" aus Config wird angezeigt', async ({ page }) => {
  const dp = await createFloatDP('default-tr')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 21.5)
  await buildChartPage(pageId, widgetId, dp.id, {
    label: 'Zeitbereich-Test',
    time_range: 'last_1h',
    primary_color: '#3b82f6',
    primary_axis: 'left',
    series: [],
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    // Dropdown muss sichtbar sein
    const select = page.locator('select[title="Zeitbereich wählen"]')
    await expect(select).toBeVisible()

    // Ausgewählter Wert muss dem Config-Default entsprechen
    await expect(select).toHaveValue('last_1h')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2: Dropdown enthält alle 18 Zeitbereich-Optionen ───────────────────

test('Verlauf-Widget: Dropdown enthält alle 18 Zeitbereich-Optionen', async ({ page }) => {
  const dp = await createFloatDP('options')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 20.0)
  await buildChartPage(pageId, widgetId, dp.id, {
    label: 'Optionen-Test',
    time_range: 'last_24h',
    primary_color: '#3b82f6',
    primary_axis: 'left',
    series: [],
  })

  const expectedValues = [
    'last_5m', 'last_15m', 'last_30m',
    'last_1h', 'last_3h', 'last_6h', 'last_12h', 'last_24h',
    'last_2d', 'last_7d', 'last_30d', 'last_90d',
    'today', 'this_week', 'this_month',
    'yesterday', 'last_week', 'last_month',
  ]

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    const select = page.locator('select[title="Zeitbereich wählen"]')
    await expect(select).toBeVisible()

    const optionCount = await select.locator('option').count()
    expect(optionCount).toBe(expectedValues.length)

    for (const val of expectedValues) {
      await expect(select.locator(`option[value="${val}"]`)).toBeAttached()
    }
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 3: Auswahl eines anderen Zeitbereichs löst Neuladung aus ───────────

test('Verlauf-Widget: Auswahl eines anderen Zeitbereichs aktualisiert Chart ohne Fehler', async ({ page }) => {
  const dp = await createFloatDP('switch-tr')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 18.0)
  await buildChartPage(pageId, widgetId, dp.id, {
    label: 'Wechsel-Test',
    time_range: 'last_24h',
    primary_color: '#3b82f6',
    primary_axis: 'left',
    series: [],
  })

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    await page.goto(`/visu/${pageId}`)

    const canvas = page.locator('canvas').first()
    await expect(canvas).toBeVisible({ timeout: 8000 })

    const select = page.locator('select[title="Zeitbereich wählen"]')
    await expect(select).toBeVisible()

    // Zeitbereich auf "Letzte 7 Tage" wechseln
    await select.selectOption('last_7d')
    await expect(select).toHaveValue('last_7d')

    // Kurz warten, damit die Neuladung stattfindet
    await page.waitForTimeout(1000)

    // Canvas muss noch sichtbar und fehlerfrei sein
    await expect(canvas).toBeVisible()
    const chartErrors = errors.filter(e =>
      e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'),
    )
    expect(chartErrors).toHaveLength(0)

    // Nochmals wechseln — auf "Heute bis jetzt"
    await select.selectOption('today')
    await expect(select).toHaveValue('today')
    await page.waitForTimeout(500)
    await expect(canvas).toBeVisible()
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 4: Rückwärtskompatibilität — alte Config ohne time_range ───────────

test('Verlauf-Widget: alte Config ohne time_range fällt auf last_24h zurück', async ({ page }) => {
  const dp = await createFloatDP('compat')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 25.0)

  // Alte Config-Struktur mit "hours" statt "time_range"
  await buildChartPage(pageId, widgetId, dp.id, {
    label: 'Compat-Test',
    hours: 24,
    primary_color: '#3b82f6',
    primary_axis: 'left',
    series: [],
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    const select = page.locator('select[title="Zeitbereich wählen"]')
    await expect(select).toBeVisible()

    // Fallback auf last_24h
    await expect(select).toHaveValue('last_24h')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
