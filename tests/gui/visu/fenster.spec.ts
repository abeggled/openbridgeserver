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

// ─── Test 8 (hoch): Zweiflügler handle_left=false → Flügel intern immer geschlossen ──

test('Zweiflügler: handle_left=false → linker Flügel immer grün (geschlossen), auch wenn Kontakt offen', async ({ page }) => {
  const dpLeft   = await createBoolDP('hl-state-left')
  const dpRight  = await createBoolDP('hl-state-right')
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

    // Linker Kontakt = offen (true), aber Griff deaktiviert → linker Flügel trotzdem grün
    await pushBool(dpLeft.id,  true)
    await pushBool(dpRight.id, false)

    const widget        = page.locator(`[data-widget-id="${widgetId}"]`)
    const coloredGroups = widget.locator('svg g[style]')

    await expect(coloredGroups.first()).toHaveCSS('color', COLOR_CLOSED, { timeout: 3_000 })
    await expect(coloredGroups.nth(1)).toHaveCSS('color', COLOR_CLOSED, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpLeft.id}`)
    await apiDelete(`/api/v1/datapoints/${dpRight.id}`)
  }
})

// ─── Test 9 (hoch): Dachflächenfenster — Position-Status steuert Anzeigefarbe ───────

test('Dachflächenfenster: dp_position_status 0%=geschlossen (grün), 50%=teiloffen (orange), 100%=offen (rot)', async ({ page }) => {
  const dpPosStatus = await apiPost('/api/v1/datapoints', {
    name: `E2E-Dach-PosStatus-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }

  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildFensterPage(pageId, widgetId, 'dachfenster', {
    dp_position_status: dpPosStatus.id,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const colorDiv = page.locator(`[data-widget-id="${widgetId}"] div`).first()

    await apiPost(`/api/v1/datapoints/${dpPosStatus.id}/value`, { value: 0 })
    await expect(colorDiv).toHaveCSS('color', COLOR_CLOSED, { timeout: 3_000 })

    await apiPost(`/api/v1/datapoints/${dpPosStatus.id}/value`, { value: 50 })
    await expect(colorDiv).toHaveCSS('color', COLOR_TILTED, { timeout: 3_000 })

    await apiPost(`/api/v1/datapoints/${dpPosStatus.id}/value`, { value: 100 })
    await expect(colorDiv).toHaveCSS('color', COLOR_OPEN, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpPosStatus.id}`)
  }
})

// ─── Test 10 (hoch): Dachflächenfenster — invert_position ────────────────────

test('Dachflächenfenster: invert_position=true → Wert 0=offen (rot), 100=geschlossen (grün)', async ({ page }) => {
  const dpPos = await apiPost('/api/v1/datapoints', {
    name: `E2E-Dach-InvPos-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }

  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildFensterPage(pageId, widgetId, 'dachfenster', {
    dp_position_status: dpPos.id,
    invert_position:    true,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const colorDiv = page.locator(`[data-widget-id="${widgetId}"] div`).first()

    // Wert 0 → invertiert = 100 = offen → rot
    await apiPost(`/api/v1/datapoints/${dpPos.id}/value`, { value: 0 })
    await expect(colorDiv).toHaveCSS('color', COLOR_OPEN, { timeout: 3_000 })

    // Wert 50 → invertiert = 50 = teiloffen → orange
    await apiPost(`/api/v1/datapoints/${dpPos.id}/value`, { value: 50 })
    await expect(colorDiv).toHaveCSS('color', COLOR_TILTED, { timeout: 3_000 })

    // Wert 100 → invertiert = 0 = geschlossen → grün
    await apiPost(`/api/v1/datapoints/${dpPos.id}/value`, { value: 100 })
    await expect(colorDiv).toHaveCSS('color', COLOR_CLOSED, { timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpPos.id}`)
  }
})

// ─── Test 11 (mittel): Dachflächenfenster — invert_shutter ───────────────────

test('Dachflächenfenster: invert_shutter=true → Rollladen-Overlay bei Wert 0 vollständig sichtbar', async ({ page }) => {
  const dpShutter = await apiPost('/api/v1/datapoints', {
    name: `E2E-Dach-InvShutter-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }

  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildFensterPage(pageId, widgetId, 'dachfenster', {
    enable_shutter:    true,
    dp_shutter_status: dpShutter.id,
    invert_shutter:    true,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)

    // Wert 0 → invertiert = 100% geschlossen → Rollladen-Overlay rect sichtbar (height > 0)
    await apiPost(`/api/v1/datapoints/${dpShutter.id}/value`, { value: 0 })
    await page.waitForTimeout(500)
    const shutterRect = widget.locator('svg rect[height]').last()
    const h = await shutterRect.getAttribute('height')
    expect(Number(h)).toBeGreaterThan(0)

    // Wert 100 → invertiert = 0% geschlossen → kein Rollladen-Overlay
    await apiPost(`/api/v1/datapoints/${dpShutter.id}/value`, { value: 100 })
    await page.waitForTimeout(500)
    await expect(widget.locator('svg rect[height="0"]')).toHaveCount(0)
    // Overlay-Rect darf nicht mehr existieren (shutterBarH = 0 → v-if="enableShutter && shutterBarH > 0")
    const shutterRects = widget.locator('svg rect.fill-gray-600, svg rect.fill-gray-500')
    await expect(shutterRects).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpShutter.id}`)
  }
})
