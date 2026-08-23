import { test, expect } from '@playwright/test'
import { randomUUID } from 'crypto'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * End-to-end test: A DataPoint value pushed via API must appear in the
 * ValueDisplay widget on the Visu page within 3 seconds (WebSocket push).
 */
test('Wert via WebSocket im Widget anzeigen', async ({ page }) => {
  // 1. Create fixtures via API
  const dp = await apiPost('/api/v1/datapoints', {
    name: `E2E-Visu-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = dp.id

  const visuNode = await apiPost('/api/v1/visu/nodes', {
    name: `E2E-Visu-Page-${Date.now()}`,
    type: 'PAGE',
    order: 999,
    access: 'public',
  }) as { id: string }
  const pageId = visuNode.id

  // Add a ValueDisplay widget to the page
  const widgetId = randomUUID()
  await apiPut(`/api/v1/visu/pages/${pageId}`, {
    grid_cols: 12,
    grid_row_height: 80,
    grid_cell_width: 80,
    background: null,
    widgets: [
      {
        id: widgetId,
        name: 'Temp Widget',
        type: 'ValueDisplay',
        datapoint_id: dpId,
        status_datapoint_id: null,
        x: 0,
        y: 0,
        w: 3,
        h: 2,
        config: {},
      },
    ],
  })

  try {
    // 2. Navigate to the visu page
    await page.goto(`/visu/${pageId}`)
    await page.waitForLoadState('networkidle')

    // 3. Push value via API
    const targetValue = 23.1
    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: targetValue })

    // 4. Widget must show "23.1" within 3 s
    const widgetValue = page.locator(`[data-dp="${dpId}"] [data-testid="widget-value"]`)
    // Decimal separator follows the configured regional format — the OBS
    // default language `de` resolves to de-DE (issue #1073).
    await expect(widgetValue).toContainText('23,1', { timeout: 3_000 })
  } finally {
    // Cleanup
    await apiDelete(`/api/v1/visu/nodes/${pageId}`)
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})
