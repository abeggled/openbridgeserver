import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete, apiUploadIcon, apiDeleteIcons, getToken } from '../helpers'

/**
 * E2E-Tests für das Link-Widget.
 *
 * Getestete Szenarien:
 *   1. Widget zeigt Emoji-Icon korrekt an
 *   2. Widget zeigt importiertes SVG-Icon als Blob-`img` an
 */

async function createVisuPage(name: string) {
  return (await apiPost('/api/v1/visu/nodes', {
    name,
    type: 'PAGE',
    order: 999,
    access: 'public',
  })) as { id: string }
}

async function buildLinkPage(pageId: string, widgetId: string, icon: string, targetId = '') {
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id: widgetId,
        name: 'E2E Link',
        type: 'Link',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0, y: 0, w: 3, h: 2,
        config: { label: 'Test-Link', icon, target_node_id: targetId },
      },
    ],
  })
}

// ─── Test 1: Emoji-Icon ───────────────────────────────────────────────────────

test('Link: Emoji-Icon wird als Text gerendert', async ({ page }) => {
  const visuNode = await createVisuPage(`E2E-Link-Emoji-${Date.now()}`)
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await buildLinkPage(pageId, widgetId, '🏠')

  try {
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible()

    const iconSpan = widget.locator('[data-testid="link-icon"]')
    await expect(iconSpan).toBeVisible()
    await expect(iconSpan).toContainText('🏠')
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
  }
})

// ─── Test 2: SVG-Icon ─────────────────────────────────────────────────────────

test('Link: importiertes SVG-Icon wird als <img src="blob:..."> gerendert', async ({ page }) => {
  const iconName = `e2e-link-icon-${Date.now()}`
  const minimalSvg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="2" y="2" width="20" height="20"/></svg>'

  await apiUploadIcon(iconName, minimalSvg)

  const visuNode = await createVisuPage(`E2E-Link-SVG-${Date.now()}`)
  const pageId = visuNode.id
  const widgetId = randomUUID()

  await buildLinkPage(pageId, widgetId, `svg:${iconName}`)

  try {
    // Inject visu_jwt so the visu frontend can call the icon API
    const token = await getToken()
    await page.goto(`/visu/${pageId}`)
    await page.evaluate((t) => localStorage.setItem('visu_jwt', t), token)
    await page.reload()
    await page.waitForLoadState('networkidle')

    const widget = page.locator(`[data-widget-id="${widgetId}"]`)
    await expect(widget).toBeVisible()

    const iconSpan = widget.locator('[data-testid="link-icon"]')
    await expect(iconSpan).toBeVisible()

    const svgImg = iconSpan.locator('img')
    await expect(svgImg).toBeVisible({ timeout: 10_000 })
    await expect(svgImg).toHaveAttribute('src', /^blob:/)
    await expect(iconSpan.locator('svg')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDeleteIcons([iconName])
  }
})
