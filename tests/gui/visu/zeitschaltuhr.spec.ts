/**
 * Playwright E2E-Tests für das Zeitschaltuhr-Widget (Issue #302)
 *
 * Testet das Widget-Rendering im Visu-Frontend:
 *  1. Widget rendert Label und "Keine Schaltpunkte" ohne Bindings
 *  2. Widget zeigt "Zeitschaltuhr aktiv" bei aktiviertem Binding
 *  3. Widget zeigt "Zeitschaltuhr inaktiv" bei deaktiviertem Binding
 *  4. Klick auf Widget öffnet Bestätigungs-Overlay (aktivieren/deaktivieren)
 *  5. Bestätigung "Nein" schliesst Overlay ohne Statusänderung
 *  6. Widget mit Feiertagsschaltuhr-Binding (timer_type='holiday') zeigt korrekten Status
 *  7. Widget mit Datum-Fenster-Binding zeigt korrekten Status
 *
 * Auth-Hinweis: Die Visu-App liest JWT aus localStorage['visu_jwt'], der Admin-Login
 * setzt aber 'access_token'. Deshalb wird der Token via page.addInitScript() vor
 * jeder Navigation manuell als 'visu_jwt' gesetzt, damit dpApi.listBindings() auth'd läuft.
 */

import { test, expect, type Page } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiDelete, apiPut, getToken } from '../helpers'

// ---------------------------------------------------------------------------
// Auth-Hilfsfunktion
// ---------------------------------------------------------------------------

/**
 * Setzt visu_jwt in localStorage BEVOR die Seite lädt, damit der Visu-Frontend
 * dpApi.listBindings() mit JWT-Auth aufruft.
 */
async function injectVisuJwt(page: Page) {
  const token = await getToken()
  await page.addInitScript((t: string) => {
    localStorage.setItem('visu_jwt', t)
  }, token)
}

// ---------------------------------------------------------------------------
// Fixture-Hilfsfunktionen
// ---------------------------------------------------------------------------

async function createZsuInstance() {
  return await apiPost('/api/v1/adapters/instances', {
    adapter_type: 'ZEITSCHALTUHR',
    name: `E2E-ZSU-Visu-${Date.now()}`,
    config: {
      latitude: 47.5,
      longitude: 8.0,
      holiday_country: 'CH',
      custom_holidays: ['01-01:Neujahr', '08-01:Nationalfeiertag'],
    },
    enabled: false,
  }) as { id: string }
}

async function createBoolDp(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-ZSU-Visu-DP-${suffix}-${Date.now()}`,
    data_type: 'BOOLEAN',
    tags: [],
  }) as { id: string }
}

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-ZSU-Visu-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function createZsuBinding(
  dpId: string,
  instanceId: string,
  timerConfig: Record<string, unknown>,
  enabled: boolean,
) {
  return await apiPost(`/api/v1/datapoints/${dpId}/bindings`, {
    adapter_instance_id: instanceId,
    direction: 'SOURCE',
    config: {
      timer_type: 'daily',
      weekdays: [0, 1, 2, 3, 4, 5, 6],
      time_ref: 'absolute',
      hour: 8,
      minute: 0,
      offset_minutes: 0,
      every_hour: false,
      every_minute: false,
      holiday_mode: 'ignore',
      vacation_mode: 'ignore',
      selected_holidays: [],
      date_window_enabled: false,
      date_window_from: '',
      date_window_to: '',
      value: '1',
      ...timerConfig,
    },
    enabled,
  }) as { id: string }
}

async function buildZsuPage(
  pageId: string,
  widgetId: string,
  dpId: string,
  instanceId: string,
) {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id: widgetId,
        name: 'E2E Zeitschaltuhr',
        type: 'Zeitschaltuhr',
        datapoint_id: dpId,
        status_datapoint_id: null,
        x: 0, y: 0, w: 3, h: 4,
        config: {
          label: 'Testschaltuhr',
          datapoint_id: dpId,
          instance_id: instanceId,
          mode: 'full',
        },
      },
    ],
  })
}

// ---------------------------------------------------------------------------
// Test 1: Widget ohne Bindings → "Keine Schaltpunkte"
// ---------------------------------------------------------------------------

test('ZSU-Widget zeigt "Keine Schaltpunkte" wenn kein Binding existiert', async ({ page }) => {
  const dp       = await createBoolDp('no-binding')
  const instance = await createZsuInstance()
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildZsuPage(pageId, widgetId, dp.id, instance.id)
  await injectVisuJwt(page)

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible({ timeout: 8_000 })

    await expect(widget.locator('[data-testid="zsu-label"]')).toHaveText('Testschaltuhr')
    await expect(widget.locator('[data-testid="zsu-status"]')).toHaveText('Keine Schaltpunkte', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 2: Widget mit aktiviertem Binding → "Zeitschaltuhr aktiv"
// ---------------------------------------------------------------------------

test('ZSU-Widget zeigt "Zeitschaltuhr aktiv" bei enabled=true Binding', async ({ page }) => {
  const dp       = await createBoolDp('active')
  const instance = await createZsuInstance()
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await createZsuBinding(dp.id, instance.id, {}, true)
  await buildZsuPage(pageId, widgetId, dp.id, instance.id)
  await injectVisuJwt(page)

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible({ timeout: 8_000 })
    await expect(widget.locator('[data-testid="zsu-status"]')).toHaveText('Zeitschaltuhr aktiv', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 3: Widget mit deaktiviertem Binding → "Zeitschaltuhr inaktiv"
// ---------------------------------------------------------------------------

test('ZSU-Widget zeigt "Zeitschaltuhr inaktiv" bei enabled=false Binding', async ({ page }) => {
  const dp       = await createBoolDp('inactive')
  const instance = await createZsuInstance()
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await createZsuBinding(dp.id, instance.id, {}, false)
  await buildZsuPage(pageId, widgetId, dp.id, instance.id)
  await injectVisuJwt(page)

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible({ timeout: 8_000 })
    await expect(widget.locator('[data-testid="zsu-status"]')).toHaveText('Zeitschaltuhr inaktiv', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 4: Klick auf Widget öffnet Bestätigungs-Overlay
// ---------------------------------------------------------------------------

test('Klick auf inaktives ZSU-Widget öffnet Bestätigungs-Overlay, Nein schliesst es', async ({ page }) => {
  const dp       = await createBoolDp('confirm')
  const instance = await createZsuInstance()
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await createZsuBinding(dp.id, instance.id, {}, false)
  await buildZsuPage(pageId, widgetId, dp.id, instance.id)
  await injectVisuJwt(page)

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible({ timeout: 8_000 })
    await expect(widget.locator('[data-testid="zsu-status"]')).toHaveText('Zeitschaltuhr inaktiv', { timeout: 5_000 })

    // Klick auf Widget → Overlay erscheint
    await widget.click()
    const overlay = widget.locator('[data-testid="zsu-confirm-overlay"]')
    await expect(overlay).toBeVisible({ timeout: 3_000 })

    // "Nein" → Overlay verschwindet, Status unverändert
    await widget.locator('[data-testid="zsu-confirm-no"]').click()
    await expect(overlay).not.toBeVisible({ timeout: 2_000 })
    await expect(widget.locator('[data-testid="zsu-status"]')).toHaveText('Zeitschaltuhr inaktiv')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 5: Widget mit Feiertagsschaltuhr-Binding (timer_type='holiday')
// ---------------------------------------------------------------------------

test('ZSU-Widget mit Feiertagsschaltuhr-Binding (timer_type=holiday) zeigt korrekten Status', async ({ page }) => {
  const dp       = await createBoolDp('holiday-type')
  const instance = await createZsuInstance()
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await createZsuBinding(dp.id, instance.id, {
    timer_type: 'holiday',
    selected_holidays: ['Neujahr'],
  }, true)
  await buildZsuPage(pageId, widgetId, dp.id, instance.id)
  await injectVisuJwt(page)

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible({ timeout: 8_000 })
    await expect(widget.locator('[data-testid="zsu-status"]')).toHaveText('Zeitschaltuhr aktiv', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 6: Widget mit Datum-Fenster-Binding
// ---------------------------------------------------------------------------

test('ZSU-Widget mit Datum-Fenster-Binding (easter-7 bis easter+7) zeigt korrekten Status', async ({ page }) => {
  const dp       = await createBoolDp('datwin')
  const instance = await createZsuInstance()
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await createZsuBinding(dp.id, instance.id, {
    date_window_enabled: true,
    date_window_from: 'easter-7',
    date_window_to: 'easter+7',
  }, true)
  await buildZsuPage(pageId, widgetId, dp.id, instance.id)
  await injectVisuJwt(page)

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible({ timeout: 8_000 })
    // Binding ist enabled=true → "aktiv" (Datum-Fenster unterdrückt nur das Feuern, nicht das enabled-Flag)
    await expect(widget.locator('[data-testid="zsu-status"]')).toHaveText('Zeitschaltuhr aktiv', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})
