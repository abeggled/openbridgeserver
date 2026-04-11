import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E-Tests für das Fenster / Türe Widget.
 *
 * Priorität hoch:
 *   1. Kontakt false → geschlossen (grün), Kontakt true → offen (rot)
 *   2. Kipp-Sensor true → gekippt (orange)
 *   3. Invertierung: invert_contact=true, Kontakt false → offen (rot)
 *   4. Kein DataPoint konfiguriert → Zustand unbekannt (? + grau)
 *   5. Zweiflügler: linker und rechter Flügel mit unabhängigen Farben
 *
 * Priorität mittel:
 *   6. Custom-Farbe für Zustand «offen» wird korrekt angewendet
 *   7. handle_left=false → kein Griff-Kreis im linken Flügel
 */

// ─── Standard-RGB-Farben ─────────────────────────────────────────────────────
const COLOR_CLOSED  = 'rgb(22, 163, 74)'    // #16a34a  grün
const COLOR_TILTED  = 'rgb(249, 115, 22)'   // #f97316  orange
const COLOR_OPEN    = 'rgb(239, 68, 68)'    // #ef4444  rot
const COLOR_UNKNOWN = 'rgb(156, 163, 175)'  // #9ca3af  grau

// ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

async function createBoolDP(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-Fenster-${suffix}-${Date.now()}`,
    data_type: 'BOOLEAN',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Fenster-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function pushBool(dpId: string, value: boolean) {
  await apiPost(`/api/v1/datapoints/${dpId}/value`, { value })
}

async function buildFensterPage(
  pageId: string,
  widgetId: string,
  mode: string,
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
        name: 'E2E Fenster',
        type: 'Fenster',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 3, h: 4,
        config: { label: 'Test', mode, ...config },
      },
    ],
  })
}

// ─── Test 1 (hoch): Kontaktzustand → Farbe ───────────────────────────────────

test('Fenster: Kontakt false → geschlossen (grün), Kontakt true → offen (rot)', async ({ page }) => {
  const dp = await createBoolDP('contact')
  const visuNode = await createVisuPage()
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await buildFensterPage(pageId, widgetId, 'fenster', { dp_contact: dp.id })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const colorDiv = page.locator(`[data-widget-id="${widgetId}"] div`).first()

    // Kontakt false = geschlossen → grün
    await pushBool(dp.id, false)
    await expect(colorDiv).toHaveCSS('color', COLOR_CLOSED, { timeout: 3_000 })

    // Kontakt true = offen → rot
    await pushBool(dp.id, true)
    await expect(colorDiv).toHaveCSS('color', COLOR_OPEN, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 2 (hoch): Kipp-Sensor → orange ─────────────────────────────────────

test('Fenster: Kipp-Sensor true → gekippt (orange)', async ({ page }) => {
  const dpContact = await createBoolDP('contact-tilt')
  const dpTilt    = await createBoolDP('tilt')
  const visuNode  = await createVisuPage()
  const pageId    = visuNode.id
  const widgetId  = randomUUID()

  await buildFensterPage(pageId, widgetId, 'fenster', {
    dp_contact: dpContact.id,
    dp_tilt:    dpTilt.id,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const colorDiv = page.locator(`[data-widget-id="${widgetId}"] div`).first()

    // Kontakt false, Kipp true → Kipp hat Vorrang → orange
    await pushBool(dpContact.id, false)
    await pushBool(dpTilt.id, true)
    await expect(colorDiv).toHaveCSS('color', COLOR_TILTED, { timeout: 3_000 })

    // Kipp false → zurück zu geschlossen → grün
    await pushBool(dpTilt.id, false)
    await expect(colorDiv).toHaveCSS('color', COLOR_CLOSED, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpContact.id}`)
    await apiDelete(`/api/v1/datapoints/${dpTilt.id}`)
  }
})

// ─── Test 3 (hoch): Invertierung ─────────────────────────────────────────────

test('Fenster: invert_contact=true → Kontakt false = offen (rot), true = geschlossen (grün)', async ({ page }) => {
  const dp       = await createBoolDP('inv')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildFensterPage(pageId, widgetId, 'fenster', {
    dp_contact:      dp.id,
    invert_contact:  true,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const colorDiv = page.locator(`[data-widget-id="${widgetId}"] div`).first()

    // Kontakt false → invertiert = offen → rot
    await pushBool(dp.id, false)
    await expect(colorDiv).toHaveCSS('color', COLOR_OPEN, { timeout: 3_000 })

    // Kontakt true → invertiert = geschlossen → grün
    await pushBool(dp.id, true)
    await expect(colorDiv).toHaveCSS('color', COLOR_CLOSED, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 4 (hoch): Kein DataPoint → unbekannt ───────────────────────────────

test('Fenster: kein DataPoint konfiguriert → Zustand unbekannt (? und grau)', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  // Kein dp_contact, kein dp_tilt → Zustand unknown
  await buildFensterPage(pageId, widgetId, 'fenster', {})

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget   = page.locator(`[data-widget-id="${widgetId}"]`)
    const colorDiv = widget.locator('div').first()

    // Fragezeichen sichtbar
    await expect(widget.locator('svg text')).toContainText('?')

    // Farbe grau
    await expect(colorDiv).toHaveCSS('color', COLOR_UNKNOWN)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 5 (hoch): Zweiflügler — unabhängige Flügelfarben ───────────────────

test('Zweiflügler: linker Flügel offen (rot), rechter Flügel geschlossen (grün) — unabhängige Rahmenfarben', async ({ page }) => {
  const dpLeft   = await createBoolDP('left')
  const dpRight  = await createBoolDP('right')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildFensterPage(pageId, widgetId, 'fenster_2', {
    dp_contact_left:  dpLeft.id,
    dp_contact_right: dpRight.id,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    // Linker Flügel offen, rechter geschlossen
    await pushBool(dpLeft.id,  true)
    await pushBool(dpRight.id, false)

    // Die zwei gefärbten <g>-Elemente im SVG tragen je eine style-Farbe
    const widget        = page.locator(`[data-widget-id="${widgetId}"]`)
    const coloredGroups = widget.locator('svg g[style]')

    // Linker Rahmen = rot (offen)
    await expect(coloredGroups.first()).toHaveCSS('color', COLOR_OPEN, { timeout: 3_000 })
    // Rechter Rahmen = grün (geschlossen)
    await expect(coloredGroups.nth(1)).toHaveCSS('color', COLOR_CLOSED, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpLeft.id}`)
    await apiDelete(`/api/v1/datapoints/${dpRight.id}`)
  }
})

// ─── Test 6 (mittel): Custom-Farbe für Zustand «offen» ───────────────────────

test('Fenster: konfigurierte Farbe color_open=#ff00ff wird bei geöffnetem Fenster angewendet', async ({ page }) => {
  const dp       = await createBoolDP('custom-color')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildFensterPage(pageId, widgetId, 'fenster', {
    dp_contact: dp.id,
    color_open: '#ff00ff',
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const colorDiv = page.locator(`[data-widget-id="${widgetId}"] div`).first()

    await pushBool(dp.id, true)  // offen
    await expect(colorDiv).toHaveCSS('color', 'rgb(255, 0, 255)', { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

// ─── Test 7 (mittel): handle_left=false → kein Griff im linken Flügel ────────

test('Zweiflügler: handle_left=false → nur rechter Flügel hat Griff-Kreis', async ({ page }) => {
  const dpLeft   = await createBoolDP('hl-left')
  const dpRight  = await createBoolDP('hl-right')
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildFensterPage(pageId, widgetId, 'fenster_2', {
    dp_contact_left:  dpLeft.id,
    dp_contact_right: dpRight.id,
    handle_left:      false,
    handle_right:     true,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    // Beide Flügel geschlossen → Griffe würden in closed-State erscheinen
    await pushBool(dpLeft.id,  false)
    await pushBool(dpRight.id, false)

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)

    // Kurz warten bis Vue re-rendert
    await page.waitForTimeout(500)

    // Mit handle_left=false darf es nur noch 1 Griff-Kreis geben (rechts)
    await expect(widget.locator('svg circle')).toHaveCount(1, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpLeft.id}`)
    await apiDelete(`/api/v1/datapoints/${dpRight.id}`)
  }
})
