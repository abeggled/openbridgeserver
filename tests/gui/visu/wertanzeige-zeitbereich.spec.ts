import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für die Verlaufsansicht des Wertanzeige-Widgets mit variablem
 * Zeitbereich (analog Issue #413, Verlauf-Widget).
 *
 * Verhalten:
 *   - Kleinformat-Widget: kein Zeitbereich-Dropdown, immer der Config-Wert
 *   - Modal: Zeitbereich-Dropdown mit allen 18 Optionen
 *
 * Getestete Szenarien:
 *   1. Kleinformat zeigt keinen Zeitbereich-Dropdown
 *   2. Modal: Dropdown vorhanden und zeigt den konfigurierten Standard
 *   3. Modal: Dropdown enthält alle 18 Optionen
 *   4. Modal: Zeitbereichswechsel aktualisiert Chart ohne Fehler
 *   5. Modal: wird beim erneuten Öffnen auf Config-Default zurückgesetzt
 *   6. Rückwärtskompatibilität: Widget ohne history_time_range zeigt last_24h im Modal
 *   7. Automatische Aktualisierung via WebSocket
 */

// ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

async function createFloatDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-ValDisp-${suffix}-${Date.now()}`,
    data_type: 'FLOAT',
    unit: '°C',
    record_history: true,
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-ValDisp-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushValue(dpId: string, value: number) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

async function buildValueDisplayPage(
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
        name: 'E2E ValueDisplay Zeitbereich',
        type: 'ValueDisplay',
        datapoint_id: dpId,
        status_datapoint_id: null,
        x: 0, y: 0, w: 4, h: 3,
        config,
      },
    ],
  })
}

const DEFAULT_RULES = [
  { fn: 'default', threshold: '', icon: '🌡️', color: '#3b82f6', output_type: 'value', calculation: '', prefix: '', text: '', decimals: 1, postfix: '' },
]

// ─── Test 1: Kleinformat zeigt keinen Zeitbereich-Dropdown ───────────────────

test('Wertanzeige-Verlauf: Kleinformat zeigt kein Zeitbereich-Dropdown', async ({ page }) => {
  const dp = await createFloatDP('no-dropdown')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 22.0)
  await buildValueDisplayPage(pageId, widgetId, dp.id, {
    label: 'Kein-Dropdown-Test',
    mode: 'history',
    rules: DEFAULT_RULES,
    history_time_range: 'last_3h',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    // Im Kleinformat darf kein Dropdown sichtbar sein
    await expect(page.locator('select[title="Zeitbereich wählen"]')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2: Modal zeigt Dropdown mit Config-Default ─────────────────────────

test('Wertanzeige-Verlauf: Modal zeigt Dropdown mit konfiguriertem Standard', async ({ page }) => {
  const dp = await createFloatDP('modal-default')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 20.0)
  await buildValueDisplayPage(pageId, widgetId, dp.id, {
    label: 'Modal-Default-Test',
    mode: 'history',
    rules: DEFAULT_RULES,
    history_time_range: 'last_3h',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    // Modal öffnen
    await page.locator('canvas').first().click()
    await expect(page.locator('[class*="fixed inset-0"]')).toBeVisible({ timeout: 3000 })

    const modalSelect = page.locator('[class*="fixed inset-0"] select[title="Zeitbereich wählen"]')
    await expect(modalSelect).toBeVisible()
    await expect(modalSelect).toHaveValue('last_3h')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 3: Modal-Dropdown enthält alle 18 Optionen ─────────────────────────

test('Wertanzeige-Verlauf: Modal-Dropdown enthält alle 18 Zeitbereich-Optionen', async ({ page }) => {
  const dp = await createFloatDP('modal-options')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 20.0)
  await buildValueDisplayPage(pageId, widgetId, dp.id, {
    label: 'Optionen-Test',
    mode: 'history',
    rules: DEFAULT_RULES,
    history_time_range: 'last_24h',
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

    await page.locator('canvas').first().click()
    await expect(page.locator('[class*="fixed inset-0"]')).toBeVisible({ timeout: 3000 })

    const modalSelect = page.locator('[class*="fixed inset-0"] select[title="Zeitbereich wählen"]')
    await expect(modalSelect).toBeVisible()

    const optionCount = await modalSelect.locator('option').count()
    expect(optionCount).toBe(expectedValues.length)

    for (const val of expectedValues) {
      await expect(modalSelect.locator(`option[value="${val}"]`)).toBeAttached()
    }
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 4: Zeitbereichswechsel im Modal aktualisiert Chart ─────────────────

test('Wertanzeige-Verlauf: Zeitbereichswechsel im Modal aktualisiert Chart ohne Fehler', async ({ page }) => {
  const dp = await createFloatDP('modal-switch')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 18.5)
  await buildValueDisplayPage(pageId, widgetId, dp.id, {
    label: 'Wechsel-Test',
    mode: 'history',
    rules: DEFAULT_RULES,
    history_time_range: 'last_24h',
  })

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    await page.locator('canvas').first().click()
    await expect(page.locator('[class*="fixed inset-0"]')).toBeVisible({ timeout: 3000 })

    const modalSelect = page.locator('[class*="fixed inset-0"] select[title="Zeitbereich wählen"]')
    await expect(modalSelect).toBeVisible()

    // Auf "Letzte 7 Tage" wechseln
    await modalSelect.selectOption('last_7d')
    await expect(modalSelect).toHaveValue('last_7d')
    await page.waitForTimeout(1000)
    await expect(page.locator('[class*="fixed inset-0"] canvas')).toBeVisible()

    // Auf "Heute bis jetzt" wechseln
    await modalSelect.selectOption('today')
    await expect(modalSelect).toHaveValue('today')
    await page.waitForTimeout(500)

    const chartErrors = errors.filter(e =>
      e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'),
    )
    expect(chartErrors).toHaveLength(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 5: Modal-Zeitbereich wird beim Öffnen auf Config-Default zurückgesetzt

test('Wertanzeige-Verlauf: Modal-Dropdown wird beim erneuten Öffnen auf Config-Default zurückgesetzt', async ({ page }) => {
  const dp = await createFloatDP('modal-reset')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 19.0)
  await buildValueDisplayPage(pageId, widgetId, dp.id, {
    label: 'Reset-Test',
    mode: 'history',
    rules: DEFAULT_RULES,
    history_time_range: 'last_1h',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    // Modal öffnen und Zeitbereich wechseln
    await page.locator('canvas').first().click()
    await expect(page.locator('[class*="fixed inset-0"]')).toBeVisible({ timeout: 3000 })
    const modalSelect = page.locator('[class*="fixed inset-0"] select[title="Zeitbereich wählen"]')
    await modalSelect.selectOption('last_7d')
    await expect(modalSelect).toHaveValue('last_7d')

    // Modal schliessen
    await page.locator('button:has-text("✕")').click()
    await expect(page.locator('[class*="fixed inset-0"]')).toHaveCount(0)

    // Modal erneut öffnen — soll wieder auf Config-Default zeigen
    await page.locator('canvas').first().click()
    await expect(page.locator('[class*="fixed inset-0"]')).toBeVisible({ timeout: 3000 })
    const resetSelect = page.locator('[class*="fixed inset-0"] select[title="Zeitbereich wählen"]')
    await expect(resetSelect).toHaveValue('last_1h')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 6: Rückwärtskompatibilität — alte Config ohne history_time_range ───

test('Wertanzeige-Verlauf: alte Config ohne history_time_range zeigt last_24h im Modal', async ({ page }) => {
  const dp = await createFloatDP('compat')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 25.0)
  await buildValueDisplayPage(pageId, widgetId, dp.id, {
    label: 'Compat-Test',
    mode: 'history',
    rules: DEFAULT_RULES,
    history_hours: 24, // alte Config-Struktur
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 8000 })

    await page.locator('canvas').first().click()
    await expect(page.locator('[class*="fixed inset-0"]')).toBeVisible({ timeout: 3000 })

    const modalSelect = page.locator('[class*="fixed inset-0"] select[title="Zeitbereich wählen"]')
    await expect(modalSelect).toBeVisible()
    await expect(modalSelect).toHaveValue('last_24h')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 7: Automatische Aktualisierung via WebSocket ───────────────────────

test('Wertanzeige-Verlauf: Canvas bleibt nach WS-Aktualisierung sichtbar und fehlerfrei', async ({ page }) => {
  const dp = await createFloatDP('ws-refresh')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await pushValue(dp.id, 20.0)
  await buildValueDisplayPage(pageId, widgetId, dp.id, {
    label: 'WS-Test',
    mode: 'history',
    rules: DEFAULT_RULES,
    history_time_range: 'last_1h',
  })

  const errors: string[] = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })

  try {
    await page.goto(`/visu/${pageId}`)
    const canvas = page.locator('canvas').first()
    await expect(canvas).toBeVisible({ timeout: 8000 })

    // Neuen Wert via API senden (simuliert WS-Update)
    await pushValue(dp.id, 21.5)

    // Widget wartet 2 s nach WS-Nachricht bevor es neu lädt
    await page.waitForTimeout(3500)

    await expect(canvas).toBeVisible()
    const chartErrors = errors.filter(e =>
      e.toLowerCase().includes('chart') || e.toLowerCase().includes('cannot'),
    )
    expect(chartErrors).toHaveLength(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
