import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das RTR-Widget (Issue #59).
 *
 * Testsuite deckt ab:
 *   1. Grunddarstellung: Widget rendert, Arc und Sollwert-Text sind sichtbar
 *   2. +/- Buttons sind vorhanden und aktiv wenn DP konfiguriert
 *   3. Betriebsart-Buttons erscheinen wenn mode_dp_id gesetzt und show_modes=true
 *   4. Ohne DP sind die +/- Buttons deaktiviert
 */

// ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

async function createFloatDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-RTR-${suffix}-${Date.now()}`,
    data_type: 'FLOAT',
    unit: '°C',
    tags: [],
  }) as { id: string }
}

async function createIntDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-RTR-Mode-${suffix}-${Date.now()}`,
    data_type: 'INTEGER',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-RTR-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function buildRTRPage(
  pageId: string,
  widgetId: string,
  dpId: string | null,
  config: Record<string, unknown>,
  statusDpId: string | null = null,
) {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id:                  widgetId,
        name:                'E2E RTR',
        type:                'RTR',
        datapoint_id:        dpId,
        status_datapoint_id: statusDpId,
        x: 0, y: 0, w: 3, h: 5,
        config,
      },
    ],
  })
}

// ─── Test 1: Grunddarstellung ─────────────────────────────────────────────────

test('RTR: Widget rendert, Arc und Soll-Wert sind sichtbar', async ({ page }) => {
  const dp       = await createFloatDP('soll-1')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildRTRPage(pageId, widgetId, dp.id, {
    label:      'Wohnzimmer',
    color:      '#ef4444',
    min_temp:   5,
    max_temp:   35,
    step:       0.5,
    decimals:   1,
    show_modes: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget.locator('[data-testid="rtr-widget"]')).toBeVisible({ timeout: 5_000 })
    await expect(widget.locator('[data-testid="rtr-arc"]')).toBeVisible({ timeout: 3_000 })
    await expect(widget.locator('[data-testid="rtr-setpoint-value"]')).toBeVisible({ timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2: +/- Buttons vorhanden und aktiv ─────────────────────────────────

test('RTR: Plus- und Minus-Buttons sind sichtbar und aktiv wenn DP gesetzt', async ({ page }) => {
  const dp       = await createFloatDP('soll-2')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildRTRPage(pageId, widgetId, dp.id, {
    color:      '#3b82f6',
    min_temp:   10,
    max_temp:   30,
    step:       0.5,
    decimals:   1,
    show_modes: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget   = page.locator(`[data-widget-id="${widgetId}"]`)
    const minusBtn = widget.locator('[data-testid="rtr-btn-minus"]')
    const plusBtn  = widget.locator('[data-testid="rtr-btn-plus"]')

    await expect(minusBtn).toBeVisible({ timeout: 5_000 })
    await expect(plusBtn).toBeVisible({ timeout: 3_000 })
    await expect(minusBtn).not.toBeDisabled()
    await expect(plusBtn).not.toBeDisabled()
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 3: Betriebsart-Buttons ─────────────────────────────────────────────

test('RTR: Betriebsart-Buttons erscheinen wenn mode_dp_id konfiguriert', async ({ page }) => {
  const dpSoll   = await createFloatDP('soll-3')
  const dpMode   = await createIntDP('mode-3')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildRTRPage(pageId, widgetId, dpSoll.id, {
    color:           '#ef4444',
    min_temp:        5,
    max_temp:        35,
    step:            0.5,
    decimals:        1,
    mode_dp_id:      dpMode.id,
    show_modes:      true,
    variant:         'ac',
    supported_modes: [0, 1, 3, 6],
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget.locator('[data-testid="rtr-mode-buttons"]')).toBeVisible({ timeout: 5_000 })

    // Alle 4 konfigurierten Betriebsart-Buttons müssen vorhanden sein
    await expect(widget.locator('[data-testid="rtr-mode-btn-0"]')).toBeVisible({ timeout: 3_000 })
    await expect(widget.locator('[data-testid="rtr-mode-btn-1"]')).toBeVisible({ timeout: 3_000 })
    await expect(widget.locator('[data-testid="rtr-mode-btn-3"]')).toBeVisible({ timeout: 3_000 })
    await expect(widget.locator('[data-testid="rtr-mode-btn-6"]')).toBeVisible({ timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpSoll.id}`)
    await apiDelete(`/api/v1/datapoints/${dpMode.id}`)
  }
})

// ─── Test 4: Ohne DP sind Buttons deaktiviert ─────────────────────────────────

test('RTR: Ohne Sollwert-DP sind +/- Buttons deaktiviert', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildRTRPage(pageId, widgetId, null, {
    color:      '#ef4444',
    min_temp:   5,
    max_temp:   35,
    step:       0.5,
    decimals:   1,
    show_modes: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget   = page.locator(`[data-widget-id="${widgetId}"]`)
    const minusBtn = widget.locator('[data-testid="rtr-btn-minus"]')
    const plusBtn  = widget.locator('[data-testid="rtr-btn-plus"]')

    await expect(minusBtn).toBeVisible({ timeout: 5_000 })
    await expect(plusBtn).toBeVisible({ timeout: 3_000 })
    await expect(minusBtn).toBeDisabled()
    await expect(plusBtn).toBeDisabled()
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 5: Nur konfigurierte Modi sichtbar ──────────────────────────────────

test('RTR: Nur konfigurierte Modi werden als Buttons angezeigt', async ({ page }) => {
  const dpSoll   = await createFloatDP('soll-5')
  const dpMode   = await createIntDP('mode-5')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildRTRPage(pageId, widgetId, dpSoll.id, {
    color:           '#10b981',
    min_temp:        5,
    max_temp:        35,
    step:            0.5,
    decimals:        1,
    mode_dp_id:      dpMode.id,
    show_modes:      true,
    variant:         'ac',
    supported_modes: [1, 3],   // nur Heizen + Kühlen
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget.locator('[data-testid="rtr-mode-buttons"]')).toBeVisible({ timeout: 5_000 })

    // Nur Heizen (1) und Kühlen (3) sichtbar
    await expect(widget.locator('[data-testid="rtr-mode-btn-1"]')).toBeVisible({ timeout: 3_000 })
    await expect(widget.locator('[data-testid="rtr-mode-btn-3"]')).toBeVisible({ timeout: 3_000 })

    // Auto (0) und Aus (6) dürfen nicht vorhanden sein
    await expect(widget.locator('[data-testid="rtr-mode-btn-0"]')).not.toBeVisible()
    await expect(widget.locator('[data-testid="rtr-mode-btn-6"]')).not.toBeVisible()
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpSoll.id}`)
    await apiDelete(`/api/v1/datapoints/${dpMode.id}`)
  }
})
