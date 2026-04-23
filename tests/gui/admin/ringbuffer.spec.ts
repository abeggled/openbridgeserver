import { test, expect } from '@playwright/test'
import { apiPost, apiDelete } from '../helpers'

test('RingBuffer Live-Eintrag ohne Reload', async ({ page }) => {
  // Fixture: create a DataPoint
  const created = await apiPost('/api/v1/datapoints', {
    name: `E2E-RB-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await page.goto('/ringbuffer')
    await page.waitForLoadState('networkidle')

    // Status badge must say "Live"
    await expect(page.locator('[data-testid="status-badge"]')).toContainText('Live', { timeout: 8_000 })

    // Filter by our DataPoint ID so the view only shows entries for this DP.
    // This avoids the 500-entry cap making before == after when the buffer is full.
    await page.fill('[data-testid="input-filter"]', dpId)
    await page.waitForTimeout(500) // debounce ~350 ms + server round-trip

    // Before the push, no entries for this brand-new DP should exist
    const before = await page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpId}"]`).count()

    // Push a value via API — server broadcasts ringbuffer_entry via WS
    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 42.0, quality: 'good' })

    // The WS push must add the new row within 15 s (CI environments can be slow)
    await expect(page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpId}"]`))
      .toHaveCount(before + 1, { timeout: 15_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})
