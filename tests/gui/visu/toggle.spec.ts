import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das erweiterte Schalter-Widget (Issue #180).
 *
 * Testsuite deckt ab:
 *   1. Schalter-Modus: Standard EIN/AUS-Text reagiert auf Datenpunkt-Wert
 *   2. Schalter-Modus: Benutzerdefinierter Text aus Zustandsregel
 *   3. Schalter-Modus: Toggle-Button-Farbe folgt der EIN-Konfiguration
 *   4. Nur-Icon-Modus: Icon-Farbe wechselt basierend auf Zustand
 *   5. Icon+Text-Modus: Text aus Regel angezeigt, Klick toggelt Datenpunkt
 *   6. Rückwärtskompatibilität: Alte Konfiguration ({label}) funktioniert weiterhin
 */

// ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

async function createBoolDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-Toggle-${suffix}-${Date.now()}`,
    data_type: 'BOOLEAN',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Toggle-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushBool(dpId: string, value: boolean) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

async function buildTogglePage(
  pageId: string,
  widgetId: string,
  dpId: string | null,
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
        name: 'E2E Schalter',
        type: 'Toggle',
        datapoint_id: dpId,
        status_datapoint_id: null,
        x: 0, y: 0, w: 2, h: 3,
        config,
      },
    ],
  })
}

// ─── Test 1: Schalter-Modus – EIN/AUS-Standardtext ───────────────────────────

test('Schalter switch-Modus: false → AUS, true → EIN (Standardtext)', async ({ page }) => {
  const dp       = await createBoolDP('switch-default')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildTogglePage(pageId, widgetId, dp.id, {
    label: 'Licht',
    mode: 'switch',
    on:  { icon: '', color: '#3b82f6', text: 'EIN' },
    off: { icon: '', color: '#6b7280', text: 'AUS' },
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const text = page.locator(`[data-widget-id="${widgetId}"] [data-testid="toggle-text"]`)

    // false → AUS
    await pushBool(dp.id, false)
    await expect(text).toHaveText('AUS', { timeout: 3_000 })

    // true → EIN
    await pushBool(dp.id, true)
    await expect(text).toHaveText('EIN', { timeout: 3_000 })

    // zurück auf false
    await pushBool(dp.id, false)
    await expect(text).toHaveText('AUS', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2: Schalter-Modus – benutzerdefinierter Text ───────────────────────

test('Schalter switch-Modus: custom text "AN"/"ZU" aus Konfiguration', async ({ page }) => {
  const dp       = await createBoolDP('switch-custom')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildTogglePage(pageId, widgetId, dp.id, {
    label: 'Pumpe',
    mode: 'switch',
    on:  { icon: '💧', color: '#3b82f6', text: 'AN' },
    off: { icon: '💤', color: '#6b7280', text: 'ZU' },
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const text = page.locator(`[data-widget-id="${widgetId}"] [data-testid="toggle-text"]`)

    await pushBool(dp.id, false)
    await expect(text).toHaveText('ZU', { timeout: 3_000 })

    await pushBool(dp.id, true)
    await expect(text).toHaveText('AN', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 3: Schalter-Modus – Toggle-Button-Farbe aus EIN-Konfiguration ──────

test('Schalter switch-Modus: Toggle-Schaltflächenfarbe folgt on.color (#ff0000)', async ({ page }) => {
  const dp       = await createBoolDP('switch-color')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildTogglePage(pageId, widgetId, dp.id, {
    label: 'Alarm',
    mode: 'switch',
    on:  { icon: '', color: '#ff0000', text: 'EIN' },
    off: { icon: '', color: '#6b7280', text: 'AUS' },
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const button = page.locator(`[data-widget-id="${widgetId}"] button[role="switch"]`)

    // false → grau (kein inline backgroundColor gesetzt)
    await pushBool(dp.id, false)
    await page.waitForTimeout(500)
    await expect(button).not.toHaveCSS('background-color', 'rgb(255, 0, 0)')

    // true → rot (#ff0000 = rgb(255, 0, 0))
    await pushBool(dp.id, true)
    await expect(button).toHaveCSS('background-color', 'rgb(255, 0, 0)', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 4: Nur-Icon-Modus – Farbe wechselt mit Zustand ─────────────────────

test('Schalter icon_only-Modus: Icon-Farbe wechselt zwischen EIN- und AUS-Farbe', async ({ page }) => {
  const dp       = await createBoolDP('icon-only')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  const COLOR_ON  = '#00ff00'
  const COLOR_OFF = '#ff0000'

  await buildTogglePage(pageId, widgetId, dp.id, {
    label: 'Status',
    mode: 'icon_only',
    on:  { icon: '💡', color: COLOR_ON, text: 'EIN' },
    off: { icon: '💡', color: COLOR_OFF, text: 'AUS' },
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const icon = page.locator(`[data-widget-id="${widgetId}"] [data-testid="toggle-icon"]`)

    // false → AUS-Farbe (rot)
    await pushBool(dp.id, false)
    await expect(icon).toHaveCSS('color', 'rgb(255, 0, 0)', { timeout: 3_000 })

    // true → EIN-Farbe (grün)
    await pushBool(dp.id, true)
    await expect(icon).toHaveCSS('color', 'rgb(0, 255, 0)', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 5: Icon+Text-Modus – Text sichtbar, Klick toggelt ──────────────────

test('Schalter icon_text-Modus: Text aus Regel angezeigt, Klick toggelt Datenpunkt', async ({ page }) => {
  const dp       = await createBoolDP('icon-text')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildTogglePage(pageId, widgetId, dp.id, {
    label: 'Heizung',
    mode: 'icon_text',
    on:  { icon: '🔥', color: '#ef4444', text: 'Heizen' },
    off: { icon: '❄️', color: '#3b82f6', text: 'Aus' },
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const text = page.locator(`[data-widget-id="${widgetId}"] [data-testid="toggle-text"]`)

    // Startzustand: false → "Aus"
    await pushBool(dp.id, false)
    await expect(text).toHaveText('Aus', { timeout: 3_000 })

    // true → "Heizen"
    await pushBool(dp.id, true)
    await expect(text).toHaveText('Heizen', { timeout: 3_000 })

    // Klick schaltet zurück → optimistisch "Aus"
    await page.locator(`[data-widget-id="${widgetId}"]`).click()
    await expect(text).toHaveText('Aus', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 6: Rückwärtskompatibilität – alte Konfiguration ────────────────────

test('Schalter Rückwärtskompatibilität: alte Konfiguration {label} rendert fehlerlos', async ({ page }) => {
  const dp       = await createBoolDP('legacy')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  // Nur label, keine mode/on/off → Fallback auf Standardwerte
  await buildTogglePage(pageId, widgetId, dp.id, {
    label: 'Legacy Schalter',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const text = page.locator(`[data-widget-id="${widgetId}"] [data-testid="toggle-text"]`)

    // Standardwerte: false → "AUS", true → "EIN"
    await pushBool(dp.id, false)
    await expect(text).toHaveText('AUS', { timeout: 3_000 })

    await pushBool(dp.id, true)
    await expect(text).toHaveText('EIN', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
