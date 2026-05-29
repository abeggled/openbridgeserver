import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das Stufenschalter-Widget (Issue #268).
 *
 * Testsuite deckt ab:
 *   1. Integer-Datenpunkt: Serverwert setzt aktive Stufe korrekt
 *   2. Klick schaltet zur nächsten Stufe weiter (optimistisch)
 *   3. Wrap-around: nach letzter Stufe wieder zur ersten
 *   4. Boolean-Datenpunkt: true/false-Stufen
 *   5. String-Datenpunkt: String-Werte werden korrekt zugeordnet
 */

// ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

async function createDP(suffix: string, type: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-Stufen-${suffix}-${Date.now()}`,
    data_type: type,
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Stufen-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushValue(dpId: string, value: unknown) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

async function buildPage(
  pageId: string,
  widgetId: string,
  dpId: string,
  steps: { label: string; value: string; icon: string; color: string }[],
) {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id: widgetId,
        name: 'E2E Stufenschalter',
        type: 'Stufenschalter',
        datapoint_id: dpId,
        status_datapoint_id: null,
        x: 0, y: 0, w: 2, h: 3,
        config: { label: 'Stufen-Test', steps },
      },
    ],
  })
}

// ─── Test 1: Integer-Datenpunkt — Serverwert setzt aktive Stufe ───────────────

test('Stufenschalter Integer: Serverwert bestimmt angezeigte Stufe', async ({ page }) => {
  const dp       = await createDP('int', 'INTEGER')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, dp.id, [
    { label: 'Aus',    value: '0', icon: '', color: '#6b7280' },
    { label: 'Stufe 1', value: '1', icon: '', color: '#3b82f6' },
    { label: 'Stufe 2', value: '2', icon: '', color: '#10b981' },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const label = page.locator(`[data-widget-id="${widgetId}"] [data-testid="stufenschalter-label"]`)

    await pushValue(dp.id, 0)
    await expect(label).toHaveText('Aus', { timeout: 3_000 })

    await pushValue(dp.id, 1)
    await expect(label).toHaveText('Stufe 1', { timeout: 3_000 })

    await pushValue(dp.id, 2)
    await expect(label).toHaveText('Stufe 2', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2: Klick schaltet optimistisch zur nächsten Stufe ──────────────────

test('Stufenschalter: Klick schaltet optimistisch zur nächsten Stufe', async ({ page }) => {
  const dp       = await createDP('click', 'INTEGER')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, dp.id, [
    { label: 'Niedrig', value: '0', icon: '', color: '#6b7280' },
    { label: 'Mittel',  value: '1', icon: '', color: '#3b82f6' },
    { label: 'Hoch',    value: '2', icon: '', color: '#ef4444' },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const label  = page.locator(`[data-widget-id="${widgetId}"] [data-testid="stufenschalter-label"]`)
    const widget = page.locator(`[data-widget-id="${widgetId}"]`)

    // Startzustand: Stufe 0 → "Niedrig"
    await pushValue(dp.id, 0)
    await expect(label).toHaveText('Niedrig', { timeout: 3_000 })

    // 1. Klick → "Mittel" (optimistisch)
    await widget.click()
    await expect(label).toHaveText('Mittel', { timeout: 3_000 })
    // Wait for the first write to complete — the widget blocks a second click while
    // the API call is in-flight (pending flag), so we must let it settle first.
    await page.waitForLoadState('networkidle', { timeout: 5_000 })

    // 2. Klick → "Hoch" (optimistisch)
    await widget.click()
    await expect(label).toHaveText('Hoch', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 3: Wrap-around — nach letzter Stufe zurück zur ersten ───────────────

test('Stufenschalter: Wrap-around nach letzter Stufe zur ersten', async ({ page }) => {
  const dp       = await createDP('wrap', 'INTEGER')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, dp.id, [
    { label: 'A', value: '10', icon: '', color: '#6b7280' },
    { label: 'B', value: '20', icon: '', color: '#3b82f6' },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const label  = page.locator(`[data-widget-id="${widgetId}"] [data-testid="stufenschalter-label"]`)
    const widget = page.locator(`[data-widget-id="${widgetId}"]`)

    await pushValue(dp.id, 20)
    await expect(label).toHaveText('B', { timeout: 3_000 })

    // Klick von letzter Stufe → zurück zu erster
    await widget.click()
    await expect(label).toHaveText('A', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 4: Boolean-Datenpunkt ───────────────────────────────────────────────

test('Stufenschalter Boolean: true/false-Stufen korrekt angezeigt', async ({ page }) => {
  const dp       = await createDP('bool', 'BOOLEAN')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, dp.id, [
    { label: 'Aus',  value: 'false', icon: '', color: '#6b7280' },
    { label: 'Ein',  value: 'true',  icon: '', color: '#3b82f6' },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const label = page.locator(`[data-widget-id="${widgetId}"] [data-testid="stufenschalter-label"]`)

    await pushValue(dp.id, false)
    await expect(label).toHaveText('Aus', { timeout: 3_000 })

    await pushValue(dp.id, true)
    await expect(label).toHaveText('Ein', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 5: String-Datenpunkt ────────────────────────────────────────────────

test('Stufenschalter String: String-Werte korrekt zugeordnet', async ({ page }) => {
  const dp       = await createDP('string', 'STRING')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildPage(pageId, widgetId, dp.id, [
    { label: 'Stop',     value: 'stop',    icon: '', color: '#6b7280' },
    { label: 'Langsam',  value: 'slow',    icon: '', color: '#3b82f6' },
    { label: 'Schnell',  value: 'fast',    icon: '', color: '#ef4444' },
  ])

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const label = page.locator(`[data-widget-id="${widgetId}"] [data-testid="stufenschalter-label"]`)

    await pushValue(dp.id, 'slow')
    await expect(label).toHaveText('Langsam', { timeout: 3_000 })

    await pushValue(dp.id, 'fast')
    await expect(label).toHaveText('Schnell', { timeout: 3_000 })

    await pushValue(dp.id, 'stop')
    await expect(label).toHaveText('Stop', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
