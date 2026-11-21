import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete, getToken } from '../helpers'

/**
 * E2E-Tests für das Kamera-Widget.
 *
 * Priorität hoch:
 *   1. Kein URL konfiguriert → Platzhalter "Keine URL konfiguriert"
 *   2. MJPEG-Modus → <img> mit direkter URL als src
 *   3. HLS-Modus   → <video> per hls.js (blob:), Fehler-Overlay bei unerreichbarer Playlist
 *   4. Proxy-Modus → img src zeigt auf /api/v1/camera/proxy mit _token
 *   5. Fehler-Overlay bei nicht erreichbarem Stream
 *
 * Priorität mittel:
 *   6. Label-Text wird in der Kopfzeile angezeigt
 *   7. Snapshot-Modus → img src enthält Cache-Buster-Parameter _t
 */

// ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

async function createVisuPage() {
  return await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Kamera-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
}

async function buildKameraPage(
  pageId: string,
  widgetId: string,
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
        name: 'E2E Kamera',
        type: 'Kamera',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 6, h: 4,
        config,
      },
    ],
  })
}

// ─── Test 1 (hoch): Kein URL → Platzhalter ───────────────────────────────────

test('Kamera: kein URL konfiguriert → Platzhalter "Keine URL konfiguriert"', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildKameraPage(pageId, widgetId, {
    label: '', url: '', streamType: 'mjpeg', useProxy: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toContainText('Keine URL konfiguriert')
    // Kein img/video-Element gerendert
    await expect(widget.locator('img')).toHaveCount(0)
    await expect(widget.locator('video')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 2 (hoch): MJPEG → img mit direkter URL ─────────────────────────────

test('Kamera: MJPEG-Modus → <img> mit direkter Kamera-URL als src', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()
  const camUrl   = 'http://10.0.0.1/stream.mjpeg'

  await buildKameraPage(pageId, widgetId, {
    url: camUrl, streamType: 'mjpeg', authType: 'none', useProxy: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const img = page.locator(`[data-widget-id="${widgetId}"] img`)
    await expect(img).toBeAttached({ timeout: 10_000 })
    await expect(img).toHaveAttribute('src', camUrl)
    // Kein video-Element
    await expect(page.locator(`[data-widget-id="${widgetId}"] video`)).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 3 (hoch): HLS → video-Element ──────────────────────────────────────

test('Kamera: HLS-Modus → <video> spielt per hls.js, Fehler-Overlay bei unerreichbarer Playlist', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()
  const camUrl   = 'http://10.0.0.1/stream.m3u8'

  await buildKameraPage(pageId, widgetId, {
    url: camUrl, streamType: 'hls', authType: 'none', useProxy: false,
  })

  // Die Kamera gibt es nicht: Abruf abfangen und scheitern lassen (#1480). Der
  // Abruf wird festgehalten, bis die blob:-Quelle geprüft ist — scheitert er
  // sofort, zerstört der fatale Fehler hls.js und mit ihm die Quelle wieder.
  let releasePlaylist!: () => void
  const playlistReleased = new Promise<void>((resolve) => { releasePlaylist = resolve })
  await page.route(camUrl, async (route) => {
    await playlistReleased
    await route.abort('connectionrefused')
  })

  try {
    const playlistRequest = page.waitForRequest(camUrl)
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const video = page.locator(`[data-widget-id="${widgetId}"] video`)
    await expect(video).toBeAttached({ timeout: 10_000 })
    // Chromium kann MSE: hls.js lädt die Playlist selbst und hängt eine MediaSource an.
    expect((await playlistRequest).resourceType()).toBe('xhr')
    await expect(video).toHaveAttribute('src', /^blob:/)
    // Kein img-Element
    await expect(page.locator(`[data-widget-id="${widgetId}"] img`)).toHaveCount(0)
    // Fataler hls.js-Fehler → vorhandenes Fehler-Overlay mit Neuladen
    releasePlaylist()
    await expect(page.locator(`[data-widget-id="${widgetId}"] [data-testid="kamera-reload"]`)).toBeVisible({ timeout: 15_000 })
  } finally {
    releasePlaylist()
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 4 (hoch): Proxy-Modus → img src zeigt auf Proxy-Endpunkt ───────────

test('Kamera: Proxy-Modus → img src enthält /api/v1/camera/proxy und _token', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()
  const camUrl   = 'http://192.168.1.100/cam.mjpeg'
  const token    = await getToken()

  await buildKameraPage(pageId, widgetId, {
    url: camUrl, streamType: 'mjpeg', authType: 'none', useProxy: true,
  })

  try {
    // JWT vor dem Seitenload in localStorage setzen, damit der Widget-Renderer
    // localStorage.getItem('visu_jwt') beim ersten Render bereits befüllt vorfindet.
    await page.addInitScript((jwt) => {
      window.localStorage.setItem('visu_jwt', jwt)
    }, token)

    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const img = page.locator(`[data-widget-id="${widgetId}"] img`)
    await expect(img).toBeAttached({ timeout: 10_000 })
    const src = await img.getAttribute('src')

    expect(src).toContain('/api/v1/camera/proxy')
    expect(src).toContain('url=')
    expect(src).toMatch(/_token=\S+/)
    expect(src).toContain(encodeURIComponent(camUrl))
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 5 (hoch): Fehler-Overlay bei nicht erreichbarem Stream ──────────────

test('Kamera: Fehler-Overlay und Reload-Button erscheinen bei nicht erreichbarem Stream', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildKameraPage(pageId, widgetId, {
    // Port 19979: kein Server → sofortiger Ladefehler
    url: 'http://127.0.0.1:19979/unreachable.mjpeg',
    streamType: 'mjpeg',
    authType:   'none',
    useProxy:   false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)

    // Overlay-Texte sichtbar
    await expect(widget.locator('text=Stream nicht erreichbar')).toBeVisible({ timeout: 5_000 })
    await expect(widget.locator('text=Automatischer Neuversuch')).toBeVisible()

    // Reload-Button vorhanden und klickbar
    const btn = widget.locator('button', { hasText: 'Jetzt neu laden' })
    await expect(btn).toBeVisible()
    await btn.click()
    // Nach Klick: Overlay kurz weg, dann ggf. wieder (da URL weiterhin ungültig)
    // Mindestens: kein JS-Fehler nach dem Klick
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 6 (mittel): Label-Text ─────────────────────────────────────────────

test('Kamera: konfiguriertes Label wird in Kopfzeile angezeigt', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()

  await buildKameraPage(pageId, widgetId, {
    label: 'Eingangskamera', url: 'http://10.0.0.1/cam', streamType: 'mjpeg', useProxy: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toContainText('Eingangskamera')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 7 (mittel): Snapshot → _t Cache-Buster in src ──────────────────────

test('Kamera: Snapshot-Modus → img src enthält _t Cache-Buster', async ({ page }) => {
  const visuNode = await createVisuPage()
  const pageId   = visuNode.id
  const widgetId = randomUUID()
  const camUrl   = 'http://10.0.0.1/snapshot.jpg'

  await buildKameraPage(pageId, widgetId, {
    url: camUrl, streamType: 'snapshot', refreshInterval: 30, useProxy: false,
  })

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('domcontentloaded')

    const img = page.locator(`[data-widget-id="${widgetId}"] img`)
    await expect(img).toBeAttached({ timeout: 10_000 })
    const src = await img.getAttribute('src')

    expect(src).toContain('_t=')
    expect(src).toContain(camUrl)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})
